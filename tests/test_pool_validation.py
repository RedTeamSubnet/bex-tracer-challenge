"""Pool-file guards - the checks that matter when the pool is REPLACED.

Everything here fires at load time against a synthetic pool, not the real one.
That is deliberate: these are the failures a future pool could introduce, and
the current 21 entries are all valid, so testing against them proves nothing.

Each guard exists because its failure mode is silent. A duplicate name, a
`__proto__` name or a stray trailing space does not raise anywhere downstream -
it produces an extension that is scored every round and can never be answered
correctly, quietly capping MCC for every miner.
"""

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_CHALLENGE = Path(__file__).resolve().parent.parent / "src/bex_tracer/challenge"
sys.path.insert(0, str(_CHALLENGE))

from api.config import config  # noqa: E402
from api.endpoints.challenge._pool import (  # noqa: E402
    LOCK_FILE_NAME,
    load_name_to_id,
    load_pool_groups,
    load_pool_names,
)

_VALID_ID = "a" * 32
_OTHER_ID = "b" * 32


def _pool(tmp_path, monkeypatch, entries: str) -> None:
    """Point the loaders at a synthetic pool file. Entries are written the old
    single-file way for readability; their ids are split out into the lock
    file beside it, the way the real pool is stored."""
    path = tmp_path / "pool.yml"
    path.write_text("pool:\n" + textwrap.dedent(entries))
    _entries = yaml.safe_load(path.read_text())["pool"]
    (tmp_path / LOCK_FILE_NAME).write_text(yaml.safe_dump(
        {e["name"]: {"id": e["id"]} for e in _entries if "id" in e and "name" in e}
    ))
    monkeypatch.setattr(config.challenge, "pool_path", str(path))
    for loader in (load_pool_names, load_pool_groups, load_name_to_id):
        loader.cache_clear()


@pytest.fixture(autouse=True)
def _clear_caches():
    yield
    for loader in (load_pool_names, load_pool_groups, load_name_to_id):
        loader.cache_clear()


def test_a_valid_pool_loads(tmp_path, monkeypatch):
    """Guard against the guards: this shape must keep working."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader"
          group: appearance_media
        - id: {_OTHER_ID}
          name: "Adblock Plus"
          group: blockers
    """)
    assert load_pool_names() == ("Dark Reader", "Adblock Plus")
    assert load_pool_groups() == {
        "appearance_media": ("Dark Reader",),
        "blockers": ("Adblock Plus",),
    }
    assert load_name_to_id() == {"Dark Reader": _VALID_ID, "Adblock Plus": _OTHER_ID}


def test_a_missing_lock_file_names_the_published_image(tmp_path, monkeypatch):
    """The public repository has no lock file. /task must still work from a
    checkout; only staging needs the ids, and its error says where they are."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader"
          group: appearance_media
    """)
    (tmp_path / LOCK_FILE_NAME).unlink()
    assert load_pool_names() == ("Dark Reader",)
    with pytest.raises(RuntimeError, match="published image"):
        load_name_to_id()


def test_an_entry_without_a_name_gets_the_named_error_from_every_loader(tmp_path, monkeypatch):
    """`load_pool_groups()` is the first loader startup calls; it used to fail
    with a bare KeyError instead of saying what is wrong with the pool."""
    _pool(tmp_path, monkeypatch, """
        - group: blockers
    """)
    with pytest.raises(RuntimeError, match="has no name"):
        load_pool_groups()


def test_a_proto_name_is_rejected(tmp_path, monkeypatch):
    """`merged["__proto__"] = true` is a no-op in JS, so the label would never
    arrive and the extension would score false in every round."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "__proto__"
          group: blockers
    """)
    with pytest.raises(RuntimeError, match="cannot be an extension name"):
        load_pool_names()


def test_duplicate_names_are_rejected(tmp_path, monkeypatch):
    """Two entries sharing the answer key collapse into one label."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader"
          group: blockers
        - id: {_OTHER_ID}
          name: "Dark Reader"
          group: appearance_media
    """)
    with pytest.raises(RuntimeError, match="duplicate names"):
        load_pool_names()


def test_duplicate_names_are_rejected_by_the_id_map_too(tmp_path, monkeypatch):
    """`load_name_to_id()` keys its dict by name, so a duplicate would collapse
    two entries into one and the surviving id would look unique. The guard must
    not depend on `load_pool_names()` happening to be called first."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader"
          group: appearance_media
        - id: {_OTHER_ID}
          name: "Dark Reader"
          group: blockers
    """)
    with pytest.raises(RuntimeError, match="duplicate names"):
        load_name_to_id()


def test_a_name_with_surrounding_whitespace_is_rejected(tmp_path, monkeypatch):
    """Invisible in yaml and in the published JSON, but the miner's key would
    have to match it exactly."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader "
          group: blockers
    """)
    with pytest.raises(RuntimeError, match="leading or trailing whitespace"):
        load_pool_names()


def test_a_missing_name_is_rejected(tmp_path, monkeypatch):
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          group: blockers
    """)
    with pytest.raises(RuntimeError, match="has no name"):
        load_pool_names()


def test_duplicate_ids_are_rejected(tmp_path, monkeypatch):
    """Two names pointing at one directory: one of them is unreachable."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader"
          group: appearance_media
        - id: {_VALID_ID}
          name: "Adblock Plus"
          group: blockers
    """)
    with pytest.raises(RuntimeError, match="duplicate ids"):
        load_name_to_id()


def test_a_group_name_that_is_not_a_js_identifier_is_rejected(tmp_path, monkeypatch):
    """The group name becomes `window.detect_<group>` and a filename."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader"
          group: "not-an-identifier"
    """)
    with pytest.raises(RuntimeError, match="not a\\s+valid JS identifier"):
        load_pool_groups()


def test_an_entry_with_no_group_is_rejected(tmp_path, monkeypatch):
    """No file would own it, so no miner could ever answer for it."""
    _pool(tmp_path, monkeypatch, f"""
        - id: {_VALID_ID}
          name: "Dark Reader"
    """)
    with pytest.raises(RuntimeError, match="has no group"):
        load_pool_groups()


def test_an_empty_pool_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "pool.yml"
    path.write_text("pool: []\n")
    monkeypatch.setattr(config.challenge, "pool_path", str(path))
    for loader in (load_pool_names, load_pool_groups, load_name_to_id):
        loader.cache_clear()

    with pytest.raises(RuntimeError, match="empty pool"):
        load_pool_names()


def test_a_missing_pool_file_is_rejected(tmp_path, monkeypatch):
    """Names the env var, because this is what a bad `pool_path` looks like."""
    monkeypatch.setattr(config.challenge, "pool_path", str(tmp_path / "nope.yml"))
    for loader in (load_pool_names, load_pool_groups, load_name_to_id):
        loader.cache_clear()

    with pytest.raises(RuntimeError, match="extension pool not found"):
        load_pool_names()


def test_the_bait_page_loads_exactly_the_groups_the_pool_declares():
    """Miner files are written to `detections/<group>.js`, but `index.html`
    loads them with hardcoded `<script src>` tags. A group in the pool with no
    tag is never loaded - `window.detect_<group>` stays undefined and every
    extension it owns scores false in every round, for every miner. A tag with
    no group is a 404 on each page load.

    Nothing else pins these two lists together, and the failure is silent.
    """
    import re

    _root = Path(__file__).resolve().parent.parent / "src/bex_tracer/challenge"
    _html = (_root / "templates/index.html").read_text()
    _tagged = set(re.findall(r"static/detections/([a-z_]+)\.js", _html))

    _pool = _root / "extensions.yml"
    import yaml

    _groups = {e["group"] for e in yaml.safe_load(_pool.read_text())["pool"]}

    assert _tagged == _groups, (
        f"bait page and pool disagree: "
        f"not loaded={sorted(_groups - _tagged)}, "
        f"no such group={sorted(_tagged - _groups)}"
    )
    _det = _root / "templates/static/detections"
    _stubs = {p.stem for p in _det.glob("*.js")}
    assert _stubs == _groups, f"stub files disagree: {sorted(_stubs ^ _groups)}"


# -- the staging path must not be caller-controllable ------------------------


def test_the_round_tag_is_not_derived_from_the_request_id(monkeypatch):
    """The staging path decides the id Chrome gives each extension.

    `<scratch_dir>/round-<tag>/ext/<store id>` has exactly one secret in it:
    the tag. `scratch_dir` ships in the published config, the store ids are one
    Web Store search away, and the derivation is in `_browser.derive_unpacked_id`.

    `request_id` is NOT a secret - `beans_logging_fastapi` honours a
    client-supplied `X-Request-ID` header, so whoever calls /score can choose
    it. If the tag were built from it, a caller could reconstruct every staging
    path, derive the runtime ids and probe web_accessible_resources - the exact
    lookup the per-round rotation exists to kill.
    """
    from api.endpoints.challenge import service
    from api.endpoints.challenge._payload_manager import RoundRecord

    _tags: list[str] = []

    def _capture(subset, *, pool, round_tag=None, **_kw):
        _tags.append(round_tag)
        return {_n: False for _n in pool}

    monkeypatch.setattr(service, "run_round", _capture)

    _poison = "attacker-chosen-request-id"
    service._run_all_rounds(
        [RoundRecord(index=i, enabled={"Dark Reader"}) for i in range(3)],
        pool=["Dark Reader"],
        groups={"appearance_media": ("Dark Reader",)},
        id_map={"Dark Reader": "e" * 32},
        page_url="http://127.0.0.1:1/index.html",
        settings=object(),
        request_id=_poison,
    )

    assert len(_tags) == 3, "every round must get a tag"
    for _tag in _tags:
        assert _poison not in _tag, (
            f"round tag {_tag!r} carries the caller-supplied request id; "
            f"the staging path becomes predictable and id rotation is defeated"
        )

    # One nonce per run, distinguished per round - so paths differ within a run
    # and cannot be replayed across runs.
    assert len(set(_tags)) == 3, "each round needs its own staging directory"
