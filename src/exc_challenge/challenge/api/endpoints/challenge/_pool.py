"""The published extension pool - the closed world miners are told about.

Only the `pool:` block is published. `rejected:` entries stay in the file so
nobody re-adds them, and must never reach a miner or a round.
"""

import functools
from collections import Counter
from pathlib import Path

import yaml

from api.config import config


@functools.lru_cache(maxsize=1)
def load_pool_ids() -> tuple[str, ...]:
    """Extension ids from `extensions.yml`, in file order.

    Cached because the pool is baked into the image and cannot change while the
    process runs. Returns a tuple so a caller cannot mutate the cached value.
    """
    _pool_path = Path(config.challenge.pool_path)
    if not _pool_path.is_file():
        raise RuntimeError(
            f"extension pool not found at {_pool_path} - "
            f"set {config.challenge.model_config['env_prefix']}POOL_PATH"
        )

    _spec = yaml.safe_load(_pool_path.read_text(encoding="utf-8")) or {}
    _ids = [
        _entry["id"]
        for _entry in (_spec.get("pool") or [])
        if isinstance(_entry, dict) and _entry.get("id")
    ]

    if not _ids:
        raise RuntimeError(f"{_pool_path} has an empty pool")

    _duplicates = [_id for _id, _n in Counter(_ids).items() if _n > 1]
    if _duplicates:
        # A duplicate would be scored twice and silently reweight the metric.
        raise RuntimeError(f"{_pool_path} has duplicate ids: {sorted(_duplicates)}")

    return tuple(_ids)


__all__ = ["load_pool_ids"]
