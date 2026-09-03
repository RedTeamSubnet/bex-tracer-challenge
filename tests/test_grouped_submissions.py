"""Acceptance tests for grouped submissions.

Written from `docs/PLAN-grouped-submissions.md` without reading the
implementation, so they check the contract rather than what the code happens to
do. The plan's own words on what matters:

    "Failure isolation is the point of this change. If the implementation ends
    up with one throw still zeroing the round, it has failed regardless of how
    clean the rest looks."
"""

import sys
from pathlib import Path

import pytest

_CHALLENGE = Path(__file__).resolve().parent.parent / "src/exc_challenge/challenge"
sys.path.insert(0, str(_CHALLENGE))

from api.config import config  # noqa: E402
from api.endpoints.challenge import service  # noqa: E402
from api.endpoints.challenge._pool import (  # noqa: E402
    load_pool_groups,
    load_pool_ids,
)

POOL_PATH = Path(__file__).resolve().parent.parent / "extensions.yml"


@pytest.fixture(autouse=True)
def _use_the_real_pool(monkeypatch):
    monkeypatch.setattr(config.challenge, "pool_path", str(POOL_PATH))
    load_pool_ids.cache_clear()
    load_pool_groups.cache_clear()
    yield
    load_pool_ids.cache_clear()
    load_pool_groups.cache_clear()


# -- the grouping itself ----------------------------------------------------


def test_every_pool_extension_belongs_to_exactly_one_group():
    """An unassigned extension can never be predicted - no file owns it. A
    double-assigned one gets scored twice."""
    groups = load_pool_groups()
    seen: list[str] = []
    for ids in groups.values():
        seen.extend(ids)

    assert sorted(seen) == sorted(load_pool_ids())
    assert len(seen) == len(set(seen)), "an extension appears in two groups"


def test_group_names_are_valid_js_identifiers():
    """Each becomes `window.detect_<group>`; a hyphen or space breaks the call."""
    for name in load_pool_groups():
        assert name.isidentifier(), f"{name!r} cannot be part of a JS function name"


def test_rejected_extensions_are_not_in_any_group():
    import yaml

    rejected = {
        e["id"] for e in (yaml.safe_load(POOL_PATH.read_text()).get("rejected") or [])
    }
    assert rejected, "fixture assumes extensions.yml still has a rejected block"

    for name, ids in load_pool_groups().items():
        assert not (set(ids) & rejected), f"{name} contains a rejected extension"


# -- what /task publishes ---------------------------------------------------


def test_task_publishes_the_groups():
    """Miners cannot name their files without it."""
    task = service.get_task()
    groups = getattr(task, "groups", None)
    assert groups, "MinerInput must carry the grouping"
    assert set(groups) == set(load_pool_groups())

    published = [i for ids in groups.values() for i in ids]
    assert sorted(published) == sorted(task.extension_ids)


# -- the submission contract ------------------------------------------------


def _files(**overrides: str) -> list[dict]:
    """One file per group, each a valid stub, with optional per-group override."""
    out = []
    for name in load_pool_groups():
        out.append(
            {
                "file_name": f"{name}.js",
                "content": overrides.get(
                    name, f"window.detect_{name} = async () => ({{}});"
                ),
            }
        )
    return out


def _build(files: list[dict]):
    from api.endpoints.challenge.schemas import MinerOutput

    return MinerOutput(commit_files=files)


def test_a_complete_submission_is_accepted():
    assert _build(_files())


def test_file_order_does_not_matter():
    assert _build(list(reversed(_files())))


def test_a_missing_group_file_is_rejected():
    files = _files()[:-1]
    with pytest.raises(Exception):
        _build(files)


def test_an_unexpected_file_name_is_rejected():
    files = _files()
    files.append({"file_name": "extra.js", "content": "// nope"})
    with pytest.raises(Exception):
        _build(files)


def test_a_duplicate_group_file_is_rejected():
    files = _files()
    files.append(dict(files[0]))
    with pytest.raises(Exception):
        _build(files)


def test_the_line_cap_is_still_enforced_per_file():
    cap = config.challenge.submission_max_lines
    first = next(iter(load_pool_groups()))
    with pytest.raises(Exception):
        _build(_files(**{first: "\n".join("//" for _ in range(cap + 1))}))


# -- failure isolation: the reason this change exists ------------------------


def test_wrapper_calls_one_entrypoint_per_group():
    """`blockers.js` defines `detect_blockers`, per the plan's naming rule.

    The wrapper must call every group's entrypoint. Calling a single
    `detect_extensions` cannot work: no group file defines it.
    """
    from api.endpoints.challenge._browser import wrap_miner_script

    groups = list(load_pool_groups())
    js = wrap_miner_script(10.0, groups)  # type: ignore[call-arg]

    for name in groups:
        assert f"detect_{name}" in js, f"wrapper never calls detect_{name}"


def test_a_throw_in_one_group_does_not_lose_the_others():
    """THE acceptance test for this change.

    Runs the real emitted wrapper under node with one group throwing, and
    asserts the surviving groups still report. If one throw still costs every
    label, the change has not delivered what it exists for.
    """
    import json
    import subprocess
    import tempfile

    from api.endpoints.challenge._browser import wrap_miner_script

    groups = list(load_pool_groups())
    broken, *healthy = groups
    js = wrap_miner_script(10.0, groups)  # type: ignore[call-arg]

    harness = f"""
    const window = globalThis;
    {"".join(
        f'window.detect_{g} = async () => {{ throw new Error("boom"); }};'
        if g == broken else
        f'window.detect_{g} = async () => ({{"id_{g}": true}});'
        for g in groups
    )}
    const done = (payload) => {{
        console.log(JSON.stringify(payload));
        process.exit(0);
    }};
    // `.mjs` is always strict mode, where `arguments` cannot be used as a
    // binding name - so it can't be shadowed with `const arguments = ...`.
    // Calling the wrapper with `.call(null, done)` instead relies on the
    // function's own implicit `arguments` object, which is legal.
    (async function () {{
        {js}
    }}).call(null, done);
    """
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(harness)
        path = fh.name

    out = subprocess.run(
        ["node", path], capture_output=True, text=True, timeout=30
    )
    assert out.stdout.strip(), f"wrapper produced nothing. stderr:\n{out.stderr[:600]}"
    payload = json.loads(out.stdout.strip().splitlines()[-1])

    assert "__error" not in payload, (
        f"one group threw and the whole round was lost: {payload['__error'][:200]}"
    )
    merged = payload.get("ok") or {}
    for g in healthy:
        assert f"id_{g}" in merged, f"{g} reported nothing after {broken} threw"
