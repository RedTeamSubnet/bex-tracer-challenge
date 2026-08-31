"""Staging the miner's submission into the served bait page.

Same shape as `ab_sniffer` and `ada_detection`: the miner's file is written into
`templates/static/detections/`, which `index.html` loads with a `<script src>`.
The checked-in stub is restored afterwards so a miner's code never outlives the
run that submitted it.

The file name is NOT taken on trust - `MinerOutput` already pins it to exactly
`config.challenge.submission_file_name`, and `stage_detection_files()` re-checks
that the resolved path stays inside the detections directory. A `file_name` of
`../../api/config.py` would otherwise be an arbitrary file write.
"""

import shutil
from pathlib import Path

from api.logger import logger

from .schemas import MinerOutput

_SRC_DIR = Path(__file__).resolve().parents[3]
DETECTIONS_DIR = _SRC_DIR / "templates" / "static" / "detections"
_STUB_SUFFIX = ".stub"


def _safe_target(detections_dir: Path, file_name: str) -> Path:
    """Resolve `file_name` inside `detections_dir`, or raise."""
    root = detections_dir.resolve()
    target = (root / file_name).resolve()
    if target.parent != root:
        raise ValueError(f"file_name '{file_name}' escapes the detections directory")
    return target


def stage_detection_files(
    miner_output: MinerOutput, detections_dir: Path = DETECTIONS_DIR
) -> list[Path]:
    """Write the miner's files into the served tree, keeping the stubs aside.

    Returns the paths written, so the caller can restore them in a `finally`.
    """
    detections_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []

    for commit_file in miner_output.commit_files:
        target = _safe_target(detections_dir, commit_file.file_name)

        # Keep the stub so `restore_stubs()` can put the page back. Written once
        # per target: a crashed earlier run must not have its miner code
        # promoted to "the stub".
        backup = target.with_suffix(target.suffix + _STUB_SUFFIX)
        if target.is_file() and not backup.exists():
            shutil.copy2(target, backup)

        target.write_text(commit_file.content, encoding="utf-8")
        staged.append(target)

    logger.info(f"Staged {len(staged)} detection file(s) into {detections_dir}")
    return staged


def restore_stubs(
    staged: list[Path], detections_dir: Path = DETECTIONS_DIR
) -> None:
    """Put the checked-in stubs back. Never raises - it runs in a `finally`,
    where an exception would mask the real failure."""
    for target in staged:
        backup = target.with_suffix(target.suffix + _STUB_SUFFIX)
        try:
            if backup.is_file():
                shutil.copy2(backup, target)
            else:
                target.unlink(missing_ok=True)
        except Exception as err:  # noqa: BLE001
            logger.warning(f"Could not restore stub for {target.name}: {err}")


__all__ = [
    "DETECTIONS_DIR",
    "stage_detection_files",
    "restore_stubs",
]
