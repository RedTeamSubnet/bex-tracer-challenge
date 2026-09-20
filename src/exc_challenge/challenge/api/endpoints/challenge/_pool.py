"""The published extension pool - the closed world miners are told about.

Miners are given NAMES, never ids. Store ids are not published because they
are not stable at run time either: `fetch_extensions.py` does not inject
`key`, so Chrome derives a fresh id from the staging path on every round.
The name is the answer key - it is what `GET /task` publishes and what a
miner's `detect_<group>()` returns booleans for.

Ids still exist here, but only to tell `_browser.py` which unpacked directory
to stage. They stop at that boundary.

Only the `pool:` block is published. `rejected:` entries stay in the file so
nobody re-adds them, and must never reach a miner or a round.
"""

import functools
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from api.config import config

# A group name becomes `detect_<name>` in the miner's JS entrypoint, so it
# must parse as an identifier - not just be a valid dict key.
_VALID_GROUP_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

# Extension names are far less constrained than group names - they are only ever
# object keys and JSON strings, so spaces and punctuation are fine. Two things
# are not.
#
# `__proto__` is the dangerous one. The miner wrapper merges answers with
# `merged[key] = result[key]`, and assigning a boolean to `__proto__` in JS is a
# silent no-op - the label would never arrive, scoring as a permanent false for
# an extension nobody could ever get credit for. The other prototype members
# (`constructor`, `toString`, ...) are ordinary own-properties when assigned, so
# they are safe; only `__proto__` has setter semantics.
_UNSAFE_JS_KEYS = frozenset({"__proto__"})

# Control characters survive JSON but corrupt logs and make a name impossible to
# type correctly in a submission. A name is a contract with the miner; it should
# be something they can copy.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _read_pool_entries() -> list[dict[str, Any]]:
    """The `pool:` block, raw. Shared by every loader so there is one place
    that knows where `extensions.yml` lives and what "empty" means."""
    _pool_path = Path(config.challenge.pool_path)
    if not _pool_path.is_file():
        raise RuntimeError(
            f"extension pool not found at {_pool_path} - "
            f"set {config.challenge.model_config['env_prefix']}POOL_PATH"
        )

    _spec = yaml.safe_load(_pool_path.read_text(encoding="utf-8")) or {}
    _entries = [
        _entry
        for _entry in (_spec.get("pool") or [])
        if isinstance(_entry, dict) and _entry.get("id")
    ]

    if not _entries:
        raise RuntimeError(f"{_pool_path} has an empty pool")

    return _entries


@functools.lru_cache(maxsize=1)
def load_pool_names() -> tuple[str, ...]:
    """Extension names from `extensions.yml`, in file order.

    This is the published pool and the key everything above `_browser.py` is
    written in. Cached because the pool is baked into the image and cannot
    change while the process runs; a tuple so a caller cannot mutate the cache.
    """
    _names = []
    for _entry in _read_pool_entries():
        _name = _entry.get("name")
        if not _name or not str(_name).strip():
            raise RuntimeError(
                f"{config.challenge.pool_path}: pool entry '{_entry['id']}' "
                f"has no name - the name is the published answer key"
            )
        _name = str(_name)
        if _name in _UNSAFE_JS_KEYS:
            raise RuntimeError(
                f"{config.challenge.pool_path}: '{_name}' cannot be an extension "
                f"name - the miner wrapper would drop it silently, scoring it "
                f"false in every round"
            )
        if _CONTROL_CHARS.search(_name):
            raise RuntimeError(
                f"{config.challenge.pool_path}: name for '{_entry['id']}' has a "
                f"control character; a name is published to miners and has to be "
                f"typeable"
            )
        if _name != _name.strip():
            # Leading/trailing space is invisible in yaml and in the published
            # JSON, but the miner's key would have to match it exactly.
            raise RuntimeError(
                f"{config.challenge.pool_path}: name for '{_entry['id']}' has "
                f"leading or trailing whitespace: {_name!r}"
            )
        _names.append(_name)

    _duplicates = [_n for _n, _c in Counter(_names).items() if _c > 1]
    if _duplicates:
        # Two entries sharing a name collapse into one answer key: one of them
        # can never be scored correctly, and the metric is silently reweighted.
        raise RuntimeError(
            f"{config.challenge.pool_path} has duplicate names: {sorted(_duplicates)}"
        )

    return tuple(_names)


@functools.lru_cache(maxsize=1)
def load_name_to_id() -> dict[str, str]:
    """name -> store id, for staging only.

    The one place the mapping is needed is `_browser.run_round`, which has to
    know which unpacked directory under `/opt/extensions` a name refers to.
    Nothing else should call this, and the result must never reach a response
    body or the bait page.
    """
    # Validate before building, not after. Names are the keys of the dict
    # below, so a duplicate would collapse two entries into one and the
    # surviving id would look perfectly unique afterwards - the check further
    # down cannot see what the dict already swallowed. `load_pool_names()`
    # raises on duplicates, so calling it first makes this safe no matter which
    # loader a caller reaches for first. Both are cached, so it costs nothing.
    load_pool_names()

    _by_name = {}
    for _entry in _read_pool_entries():
        _by_name[str(_entry["name"])] = _entry["id"]

    _ids = list(_by_name.values())
    _duplicates = [_i for _i, _c in Counter(_ids).items() if _c > 1]
    if _duplicates:
        raise RuntimeError(
            f"{config.challenge.pool_path} has duplicate ids: {sorted(_duplicates)}"
        )

    return _by_name


@functools.lru_cache(maxsize=1)
def load_pool_groups() -> dict[str, tuple[str, ...]]:
    """group name -> extension NAMES, in file order. Same guarantees as
    `load_pool_names()`: only the `pool:` block, cached once, immutable values.

    Every pool entry must carry a `group`, and each group name must double as
    a JS identifier - it becomes the miner's `detect_<group>` entrypoint and
    its `<group>.js` filename.
    """
    _groups: dict[str, list[str]] = {}

    for _entry in _read_pool_entries():
        _group = _entry.get("group")
        if not _group:
            raise RuntimeError(
                f"{config.challenge.pool_path}: pool entry '{_entry['id']}' "
                f"has no group"
            )
        if not _VALID_GROUP_NAME.match(_group):
            raise RuntimeError(
                f"{config.challenge.pool_path}: group '{_group}' is not a "
                f"valid JS identifier - it becomes detect_{_group}"
            )
        _groups.setdefault(_group, []).append(str(_entry["name"]))

    return {_name: tuple(_ids) for _name, _ids in _groups.items()}


__all__ = ["load_pool_names", "load_name_to_id", "load_pool_groups"]
