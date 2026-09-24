"""Tests for the pure parts of the browser layer.

Launching Chrome is not unit-testable; `challenge/scripts/run_round.py` covers that path
against real extensions. What is testable here is everything that decides what
the browser is told to do and how its answer is read.
"""

import sys
from pathlib import Path

import pytest
from selenium.common.exceptions import WebDriverException

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "src/exc_challenge/challenge")
)

from api.endpoints.challenge._browser import (  # noqa: E402
    BrowserError,
    BrowserInfraError,
    BrowserSettings,
    ChromeSession,
    _BASE_ARGS,
    bait_page_args,
    normalize_predictions,
    run_round,
    wrap_miner_script,
)

POOL = ["aaaa", "bbbb", "cccc"]
GROUPS = {"group_one": ["aaaa", "bbbb"], "group_two": ["cccc"]}
# name -> store id. `run_round` speaks names; only `ChromeSession.launch` sees ids.
ID_MAP = {"aaaa": "id-aaaa", "bbbb": "id-bbbb", "cccc": "id-cccc"}


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
    assert "10000" in wrap_miner_script(10.0, GROUPS)


def test_wrapper_calls_one_entrypoint_per_group_without_embedding_source():
    """Each group's code is served as static/detections/<group>.js and loaded
    by the bait page, so the wrapper must only CALL window.detect_<group> for
    every published group. Embedding source here again would mean the
    submission runs twice, from two different places."""
    wrapped = wrap_miner_script(5.0, GROUPS)
    for group in GROUPS:
        assert f"window.detect_{group}" in wrapped
    assert "arguments[arguments.length - 1]" in wrapped


def test_wrapper_isolates_one_groups_failure_from_the_others():
    """A throw, a missing function, or a bad return in one group must be
    recorded against that group alone - never turn into a whole-round
    `__error`, which would cost every group its labels."""
    wrapped = wrap_miner_script(5.0, GROUPS)
    assert "failedGroups" in wrapped
    assert "failed_groups: failedGroups" in wrapped
    # Each group's call is wrapped in its own try/catch, not a shared one.
    assert wrapped.count("try {") == wrapped.count("catch (err) {")
    assert wrapped.count("try {") >= len(GROUPS) + 1  # +1 for the outer catch


def test_wrapper_handles_timeout_error_and_success():
    wrapped = wrap_miner_script(5.0, GROUPS)
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


def test_bait_page_on_a_hostname_resolves_to_loopback_and_stays_secure():
    assert bait_page_args("http://baitpage.test:10001/_web") == [
        "--host-resolver-rules=MAP baitpage.test 127.0.0.1",
        "--unsafely-treat-insecure-origin-as-secure=http://baitpage.test:10001",
    ]


def test_https_bait_page_resolves_to_loopback_and_trusts_its_own_certificate():
    # Real https is already a secure context, so no secure-origin grant.
    assert bait_page_args("https://baitpage.test:10443/_web") == [
        "--host-resolver-rules=MAP baitpage.test 127.0.0.1",
        "--ignore-certificate-errors",
    ]


def test_bait_page_on_loopback_needs_no_extra_flags():
    # 127.0.0.1 is already a secure context and needs no resolving.
    for url in (None, "http://127.0.0.1:10001/_web", "http://localhost:10001/_web"):
        assert bait_page_args(url) == []


def test_launch_passes_the_bait_page_through_to_chrome(settings):
    with ChromeSession(settings, "test") as session:
        args = session._build_options([], "http://baitpage.test:10001/_web").arguments
    assert "--host-resolver-rules=MAP baitpage.test 127.0.0.1" in args


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

        assert set(staged) == {"aaaa"}, "keyed by store id, not position"
        assert staged["aaaa"] != source
        assert (staged["aaaa"] / "manifest.json").is_file()


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

        _root = staged["aaaa"]
        for path in [_root, *_root.rglob("*")]:
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


# -- run_script: who gets the blame ----------------------------------------
#
# `service.py` refuses to publish a score once too many rounds fail on us, and
# it tells the two apart with `isinstance(err, BrowserInfraError)`. These two
# tests pin that classification from both sides; if either flips, the guard
# either stops firing or starts firing on honest miner failures.


class _ScriptDriver:
    """Stands in for the Selenium driver, for `run_script` only."""

    def __init__(self, outcome):
        self._outcome = outcome

    def set_script_timeout(self, _seconds):
        pass

    def execute_async_script(self, _script):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome

    def execute_script(self, _script, *_args):
        # `run_script` consults the load diagnostic when every group fails.
        # Report every file as arrived and every entrypoint as defined, i.e.
        # nothing was wrong on our side - so the failure stays the miner's.
        _groups = _args[0] if _args else []
        return {"arrived": list(_groups), "missing": [], "ran": list(_groups), "hung": []}


def test_a_dead_renderer_is_our_fault_not_the_miners(settings):
    """`tab crashed` is what shm exhaustion under concurrency looks like.

    The wrapper turns the miner's own failures into a payload, so a
    WebDriverException out of `execute_async_script` is always the browser
    dying - and it must reach `service.py` as `BrowserInfraError`, or a run we
    broke gets booked against the miner as a low score.
    """
    session = ChromeSession(settings, "test")
    session.driver = _ScriptDriver(WebDriverException("tab crashed"))

    with pytest.raises(BrowserInfraError, match="browser died"):
        session.run_script(POOL, GROUPS, budget_sec=1.0)


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"__timeout": True}, "budget"),
        ({"__error": "boom"}, "threw"),
        ("not-a-dict", "wrapper returned"),
    ],
)
def test_a_broken_submission_stays_the_miners_fault(settings, payload, expected):
    """The mirror image: these are the miner's own failures and must NOT be
    counted as infrastructure, or a genuinely broken submission would take the
    whole run down with a 500 instead of scoring 0."""
    session = ChromeSession(settings, "test")
    session.driver = _ScriptDriver(payload)

    with pytest.raises(BrowserError, match=expected) as caught:
        session.run_script(POOL, GROUPS, budget_sec=1.0)
    assert not isinstance(caught.value, BrowserInfraError)


# -- run_script: per-group failure isolation --------------------------------
#
# `wrap_miner_script` already isolates one group's throw from the rest (see
# tests/test_grouped_submissions.py, which runs the real emitted JS under
# node). These two pin the OTHER half of the contract: what `run_script` does
# with the `failed_groups` list the wrapper hands back.


def test_run_script_tolerates_some_groups_failing(settings):
    """One failed group must not cost the round - the other groups' labels
    still get recorded, and the failed group's ids fall back to False via
    `normalize_predictions`."""
    session = ChromeSession(settings, "test")
    session.driver = _ScriptDriver(
        {"ok": {"aaaa": True}, "failed_groups": ["group_two"]}
    )

    result = session.run_script(POOL, GROUPS, budget_sec=1.0)

    assert result == {"aaaa": True, "bbbb": False, "cccc": False}


def test_run_script_raises_only_when_every_group_failed(settings):
    """Indistinguishable from the whole script being broken - this is the one
    case `run_script` still refuses to publish a result for."""
    session = ChromeSession(settings, "test")
    session.driver = _ScriptDriver({"ok": {}, "failed_groups": list(GROUPS)})

    with pytest.raises(BrowserError, match="every group failed"):
        session.run_script(POOL, GROUPS, budget_sec=1.0)


def test_run_round_rejects_an_empty_subset(settings):
    with pytest.raises(BrowserError, match="empty"):
        run_round(
            set(),
            pool=POOL,
            groups=GROUPS,
            id_map=ID_MAP,
            page_url="http://x",
            settings=settings,
        )


def test_run_round_rejects_a_name_with_no_extension_behind_it(settings):
    """A published name that maps to no directory would be scored every round
    and enabled in none of them - a permanent false negative no miner can fix,
    silently capping the metric. It has to fail the round instead."""
    import api.endpoints.challenge._browser as browser

    with pytest.raises(browser.BrowserInfraError, match="no extension directory"):
        run_round(
            {"aaaa", "not-in-the-pool"},
            pool=POOL,
            groups=GROUPS,
            id_map=ID_MAP,
            page_url="http://x",
            settings=settings,
        )


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

        def launch(self, ext_ids, page_url=None):
            calls.append("launch")

        def open_page(self, page_url, settle, groups=None):
            calls.append("open_page")

        def interact(self, pause=0.0):
            calls.append("interact")
            return []

        def run_script(self, pool, groups, budget):
            calls.append("run_script")
            return {e: False for e in pool}

    monkeypatch.setattr(browser, "ChromeSession", FakeSession)
    monkeypatch.setattr(browser.time, "sleep", lambda _s: None)

    browser.run_round(
        {"aaaa"},
        pool=POOL,
        groups=GROUPS,
        id_map=ID_MAP,
        page_url="http://x",
        settings=settings,
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


def test_derive_unpacked_id_matches_real_chrome():
    """Pins the two ids a real Chrome 152 actually assigned.

    `fetch_extensions.py` no longer injects `key`, so Chrome derives the id
    from the staging path and `launch()` relies on reproducing that exactly. If
    Chrome ever changes the derivation these values break, which is the point -
    a silent drift here would make every round report every extension missing.

    Measured by staging Dark Reader into both paths inside the challenge
    container and reading the ids back from the browser profile.
    """
    import api.endpoints.challenge._browser as browser

    assert (
        browser.derive_unpacked_id(Path("/run/exc/round-AAAA/ext/dr"))
        == "laacekklnghmcmbipfoanbjnjmejfibk"
    )
    assert (
        browser.derive_unpacked_id(Path("/run/exc/round-BBBB/ext/dr"))
        == "adjcpmplfoiaeekhcleccnhcekcmkhpd"
    )


def test_the_build_gate_derives_ids_the_same_way_the_runtime_does():
    """`scripts/verify_chrome_build.py` reimplements the path -> id derivation.

    That duplication is deliberate: the gate runs as a build step with only
    Chrome and the unpacked extensions present, and importing
    `api.endpoints.challenge._browser` would drag in selenium, psutil and
    `api.config` - which reads config files that are not mounted at build time.

    What is NOT acceptable is the two drifting. If the gate derived ids
    differently it would pass a build whose runtime then reports every
    extension missing on every round. This pins them together.
    """
    import importlib.util

    import api.endpoints.challenge._browser as browser

    _gate_path = Path(__file__).resolve().parent.parent / "scripts/verify_chrome_build.py"
    _spec = importlib.util.spec_from_file_location("_vcb", _gate_path)
    _gate = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_gate)

    for path in (
        Path("/run/exc/round-AAAA/ext/dr"),
        Path("/run/exc/round-req1-0/ext/eimadpbcbfnmbkopoojfekhnkhdbieeh"),
        Path("/opt/extensions/kbfnbcaeplbcioakkpcpgfkobkghlhen"),
    ):
        assert _gate.derive_path_id(path) == browser.derive_unpacked_id(path), path


def test_the_same_extension_gets_a_different_id_each_round():
    """The whole point of dropping `key`: a miner cannot hardcode
    `chrome-extension://<id>/...` because the id does not survive the round."""
    import api.endpoints.challenge._browser as browser

    ext = "eimadpbcbfnmbkopoojfekhnkhdbieeh"
    first = browser.derive_unpacked_id(Path(f"/run/exc/round-req1-0/ext/{ext}"))
    second = browser.derive_unpacked_id(Path(f"/run/exc/round-req1-1/ext/{ext}"))

    assert first != second
    assert first != ext and second != ext, "the store id must never be the runtime id"


def test_infra_error_does_not_name_the_loaded_extensions(settings, monkeypatch):
    """`missing` and `loaded` together reconstruct the round's enabled subset.

    This message reaches the server log, which production bind-mounts out of
    the container, so it names only what FAILED - enough to diagnose a mis-keyed
    extension, without handing over the answer key.
    """
    import api.endpoints.challenge._browser as browser

    enabled = ["aaaa", "bbbb", "cccc"]
    with ChromeSession(settings, "leak") as session:
        # `_stage_extensions` returns {store id: staged path} - see the real one.
        staged = {ext_id: session.ext_root / ext_id for ext_id in enabled}
        monkeypatch.setattr(
            session, "_stage_extensions", lambda ids: {i: staged[i] for i in ids}
        )
        monkeypatch.setattr(session, "_start_driver", lambda opts: None)
        # Chrome reports PATH-DERIVED ids now, not store ids - `key` is no
        # longer injected. Only bbbb and cccc came up.
        monkeypatch.setattr(
            session,
            "_read_loaded_ids",
            lambda: {
                browser.derive_unpacked_id(staged["bbbb"]),
                browser.derive_unpacked_id(staged["cccc"]),
            },
        )

        with pytest.raises(browser.BrowserInfraError) as err:
            session.launch(enabled)

    message = str(err.value)
    assert "aaaa" in message, "the failure must be diagnosable"
    assert "bbbb" not in message, "loaded ids are the answer key"
    assert "cccc" not in message


class _RenderDriver:
    """Stands in for the driver, for `_assert_page_rendered` only."""

    def __init__(self, outcome):
        self._outcome = outcome

    def execute_script(self, _script):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def test_an_empty_bait_page_is_our_fault_not_the_miners(settings):
    """A missing script bundle still navigates with HTTP 200 and an empty body.

    Every detector then finds nothing and ALL miners score 0. Booking that
    against whoever happened to be scored would hide our own broken asset, so
    it has to arrive as `BrowserInfraError` and leave the run's denominator.
    """
    session = ChromeSession(settings, "test")
    session.driver = _RenderDriver(False)

    with pytest.raises(BrowserInfraError, match="rendered nothing"):
        session._assert_page_rendered()


def test_a_rendered_bait_page_passes(settings):
    session = ChromeSession(settings, "test")
    session.driver = _RenderDriver(True)

    session._assert_page_rendered()  # must not raise


# -- blame attribution when a load stalls -----------------------------------


class _DiagDriver:
    """Driver double whose Resource Timing answer is scripted per test."""

    def __init__(self, state):
        self._state = state

    def execute_script(self, _script, *_args):
        if isinstance(self._state, Exception):
            raise self._state
        return self._state


def _session_with(state):
    sess = ChromeSession.__new__(ChromeSession)
    sess.driver = _DiagDriver(state)
    return sess


def test_a_stalled_load_is_ours_when_our_files_never_arrived():
    """Files still in flight, and every file that did arrive ran fine. The
    submission cannot be blamed for bytes it never received."""
    sess = _session_with(
        {"arrived": ["blockers"], "missing": ["developer"], "ran": ["blockers"], "hung": []}
    )
    reason = sess._blame_for_stalled_load(["blockers", "developer"])
    assert reason and "never reached the browser" in reason


def test_a_stalled_load_is_the_miners_when_their_file_arrived_and_hung():
    """The file was delivered but never defined its entrypoint - it started
    executing and did not come back. That is the submission's doing."""
    sess = _session_with(
        {"arrived": ["blockers"], "missing": [], "ran": [], "hung": ["blockers"]}
    )
    assert sess._blame_for_stalled_load(["blockers"]) is None


def test_a_hung_file_outranks_a_missing_one():
    """A submission must not launder its own hang into an infra failure by
    also happening to race one of our slower responses."""
    sess = _session_with(
        {"arrived": ["blockers"], "missing": ["developer"], "ran": [], "hung": ["blockers"]}
    )
    assert sess._blame_for_stalled_load(["blockers", "developer"]) is None


def test_everything_arrived_and_ran_means_the_miner_is_to_blame():
    sess = _session_with(
        {"arrived": ["blockers"], "missing": [], "ran": ["blockers"], "hung": []}
    )
    assert sess._blame_for_stalled_load(["blockers"]) is None


def test_an_undiagnosable_page_does_not_excuse_the_miner():
    """If the diagnostic itself cannot run, guessing 'infra' would hand every
    submission a free pass - so it must fall back to blaming the miner."""
    from selenium.common.exceptions import WebDriverException

    assert _session_with(WebDriverException("dead"))._blame_for_stalled_load(["a"]) is None
    assert _session_with(RuntimeError("boom"))._blame_for_stalled_load(["a"]) is None
    assert _session_with("not a dict")._blame_for_stalled_load(["a"]) is None


def test_no_groups_means_no_diagnosis():
    assert _session_with({})._blame_for_stalled_load([]) is None
    assert _session_with({})._blame_for_stalled_load(None) is None
