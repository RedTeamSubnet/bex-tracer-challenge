"""Tests for the pure parts of the browser layer.

Launching Chrome is not unit-testable; `challenge/scripts/run_round.py` covers that path
against real extensions. What is testable here is everything that decides what
the browser is told to do and how its answer is read.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "src/exc_challenge/challenge")
)

from api.endpoints.challenge._browser import (  # noqa: E402
    BrowserError,
    BrowserSettings,
    ChromeSession,
    _BASE_ARGS,
    normalize_predictions,
    run_round,
    wrap_miner_script,
)

POOL = ["aaaa", "bbbb", "cccc"]


@pytest.fixture
def settings(tmp_path):
    return BrowserSettings(
        chrome_bin="/nonexistent/chrome",
        chromedriver_bin="/nonexistent/chromedriver",
        extensions_dir=str(tmp_path / "extensions"),
        scratch_dir=str(tmp_path / "scratch"),
    )


# -- normalize_predictions -------------------------------------------------


def test_normalize_fills_missing_keys_with_false():
    assert normalize_predictions({"aaaa": True}, POOL) == {
        "aaaa": True,
        "bbbb": False,
        "cccc": False,
    }


def test_normalize_drops_keys_outside_the_pool():
    result = normalize_predictions({"aaaa": True, "not_in_pool": True}, POOL)
    assert "not_in_pool" not in result


@pytest.mark.parametrize("truthy", [1, "yes", [0]])
def test_normalize_coerces_truthy_non_booleans(truthy):
    assert normalize_predictions({"aaaa": truthy}, POOL)["aaaa"] is True


@pytest.mark.parametrize("falsy", [0, "", None, []])
def test_normalize_coerces_falsy_non_booleans(falsy):
    assert normalize_predictions({"aaaa": falsy}, POOL)["aaaa"] is False


@pytest.mark.parametrize("raw", ["a string", ["a", "list"], 42, None])
def test_normalize_rejects_non_objects(raw):
    with pytest.raises(BrowserError, match="expected"):
        normalize_predictions(raw, POOL)


# -- wrap_miner_script -----------------------------------------------------


def test_wrapper_converts_budget_to_milliseconds():
    assert "10000" in wrap_miner_script("", 10.0)


def test_wrapper_embeds_miner_code_and_entrypoint():
    wrapped = wrap_miner_script("const marker = 1;", 5.0)
    assert "const marker = 1;" in wrapped
    assert "window.detect_extensions" in wrapped


def test_wrapper_handles_timeout_error_and_success():
    wrapped = wrap_miner_script("", 5.0)
    assert "__timeout" in wrapped
    assert "__error" in wrapped
    assert "ok:" in wrapped


# -- flags -----------------------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "--disable-extensions",  # loads then disables ours
        "--disable-component-extensions-with-background-pages",
        "--single-process",  # breaks MV3 service workers
        "--headless=old",  # removed in 132, could not load extensions
        "--incognito",
    ],
)
def test_base_args_exclude_extension_breaking_flags(forbidden):
    assert forbidden not in _BASE_ARGS


def test_build_options_uses_load_extension_not_add_extension(settings, tmp_path):
    ext_dir = tmp_path / "ext" / "aaaa"
    ext_dir.mkdir(parents=True)

    with ChromeSession(settings, "test") as session:
        args = session._build_options([ext_dir]).arguments

    load = [a for a in args if a.startswith("--load-extension=")]
    assert len(load) == 1
    assert str(ext_dir.resolve()) in load[0]


def test_build_options_headless_is_the_new_mode(settings, tmp_path):
    with ChromeSession(settings, "test") as session:
        assert "--headless=new" in session._build_options([]).arguments


def test_build_options_omits_headless_when_disabled(settings, tmp_path):
    headful = BrowserSettings(**{**settings.__dict__, "headless": False})
    with ChromeSession(headful, "test") as session:
        assert not [
            a
            for a in session._build_options([]).arguments
            if a.startswith("--headless")
        ]


def test_each_round_gets_its_own_profile(settings):
    with (
        ChromeSession(settings, "one") as first,
        ChromeSession(settings, "two") as second,
    ):
        assert first.profile_dir != second.profile_dir


# -- staging ---------------------------------------------------------------


def test_stage_copies_rather_than_referencing_the_source(settings, tmp_path):
    source = Path(settings.extensions_dir) / "aaaa"
    source.mkdir(parents=True)
    (source / "manifest.json").write_text("{}")

    with ChromeSession(settings, "test") as session:
        staged = session._stage_extensions(["aaaa"])

        assert len(staged) == 1
        assert staged[0] != source
        assert (staged[0] / "manifest.json").is_file()


def test_stage_makes_the_whole_copied_tree_writable(settings):
    """`copytree` preserves source modes, so chmod'ing only the copy's root
    leaves nested files read-only - and Chrome then silently fails to rewrite
    `_metadata/`, which makes static DNR a no-op with no error at all.

    The previous version of this test asserted
    `(staged[0] / "_metadata").parent`, which is just `staged[0]` - the one
    directory the old code did chmod. It could never fail.
    """
    source = Path(settings.extensions_dir) / "aaaa"
    (source / "_metadata").mkdir(parents=True)
    (source / "manifest.json").write_text("{}")
    (source / "_metadata" / "computed_hashes.json").write_text("{}")

    # A read-only source tree, as an image built with `chmod -R a+rX` produces.
    for path in sorted(source.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    source.chmod(0o555)

    with ChromeSession(settings, "test") as session:
        staged = session._stage_extensions(["aaaa"])

        for path in [staged[0], *staged[0].rglob("*")]:
            assert path.stat().st_mode & 0o200, f"not writable: {path}"


def test_stage_rejects_a_missing_extension(settings):
    Path(settings.extensions_dir).mkdir(parents=True)
    with ChromeSession(settings, "test") as session:
        with pytest.raises(BrowserError, match="fetch_extensions"):
            session._stage_extensions(["missing"])


def test_stage_rejects_an_id_containing_a_comma(settings):
    Path(settings.extensions_dir).mkdir(parents=True)
    with ChromeSession(settings, "test") as session:
        with pytest.raises(BrowserError, match="comma"):
            session._stage_extensions(["aa,bb"])


# -- lifecycle -------------------------------------------------------------


def test_scratch_is_removed_on_exit(settings):
    with ChromeSession(settings, "test") as session:
        root = session.root
        assert root.is_dir()
    assert not root.exists()


def test_run_round_rejects_an_empty_subset(settings):
    with pytest.raises(BrowserError, match="empty"):
        run_round(set(), "", pool=POOL, page_url="http://x", settings=settings)


def test_run_round_drives_the_page_before_sampling(settings, monkeypatch):
    """Password managers only inject after a real gesture, so a round that
    skips `interact()` makes that whole class of the pool undetectable."""
    import api.endpoints.challenge._browser as browser

    calls = []

    class FakeSession:
        def __init__(self, *_a, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def launch(self, ext_ids):
            calls.append("launch")

        def open_page(self, page_url, settle):
            calls.append("open_page")

        def interact(self, pause=0.0):
            calls.append("interact")
            return []

        def run_script(self, miner_js, pool, budget):
            calls.append("run_script")
            return {e: False for e in pool}

    monkeypatch.setattr(browser, "ChromeSession", FakeSession)
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)

    browser.run_round(
        {"aaaa"}, "// js", pool=POOL, page_url="http://x", settings=settings
    )

    assert calls == ["launch", "open_page", "interact", "run_script"]


def test_sweep_does_not_match_a_sibling_round_by_prefix(settings, monkeypatch):
    """`run-1` is a prefix of `run-10`. With rounds running concurrently, a
    bare substring test would let round 1's teardown SIGKILL round 10's
    browser mid-measurement."""
    import api.endpoints.challenge._browser as browser

    class FakeProc:
        def __init__(self, pid, cmdline):
            self.pid = pid
            self.info = {"pid": pid, "cmdline": cmdline}

    with ChromeSession(settings, "run-1") as session:
        sibling = str(session.root) + "0"  # .../round-run-10
        monkeypatch.setattr(
            browser.psutil,
            "process_iter",
            lambda _attrs: [
                FakeProc(1, ["chrome", f"--user-data-dir={session.root}/profile"]),
                FakeProc(2, ["chrome", f"--user-data-dir={sibling}/profile"]),
            ],
        )
        matched = {p.pid for p in session._procs_under_root()}
        # Undo before __exit__, or teardown sweeps these fakes and blows up.
        monkeypatch.undo()

    assert matched == {1}, f"swept a concurrent round's browser: {matched}"
