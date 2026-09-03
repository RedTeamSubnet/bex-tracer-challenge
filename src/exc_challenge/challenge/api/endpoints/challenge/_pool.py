"""The published extension pool - the closed world miners are told about.

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
def load_pool_ids() -> tuple[str, ...]:
    """Extension ids from `extensions.yml`, in file order.

    Cached because the pool is baked into the image and cannot change while the
    process runs. Returns a tuple so a caller cannot mutate the cached value.
    """
    _ids = [_entry["id"] for _entry in _read_pool_entries()]

    _duplicates = [_id for _id, _n in Counter(_ids).items() if _n > 1]
    if _duplicates:
        # A duplicate would be scored twice and silently reweight the metric.
        raise RuntimeError(
            f"{config.challenge.pool_path} has duplicate ids: {sorted(_duplicates)}"
        )

    return tuple(_ids)


@functools.lru_cache(maxsize=1)
def load_pool_groups() -> dict[str, tuple[str, ...]]:
    """group name -> ids, in file order. Same guarantees as `load_pool_ids()`:
    only the `pool:` block, cached once, immutable values.

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
        _groups.setdefault(_group, []).append(_entry["id"])

    return {_name: tuple(_ids) for _name, _ids in _groups.items()}


__all__ = ["load_pool_ids", "load_pool_groups"]
