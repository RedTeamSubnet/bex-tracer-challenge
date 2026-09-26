"""Staging the miner's submission into the served bait page.

Same shape as `ab_sniffer` and `ada_detection`: the miner's file is written into
`templates/static/detections/`, which `index.html` loads with a `<script src>`.
A clean stub is written back afterwards so a miner's code never outlives the
run that submitted it.

The file name is NOT taken on trust - `MinerOutput` already pins the submitted
file names to exactly `{group}.js` for each published group, and
`stage_detection_files()` re-checks that the resolved path stays inside the
detections directory. A `file_name` of `../../api/config.py` would otherwise be
an arbitrary file write.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from api.logger import logger

if TYPE_CHECKING:  # schemas imports this module for `stub_source`
    from .schemas import MinerOutput

_SRC_DIR = Path(__file__).resolve().parents[3]
DETECTIONS_DIR = _SRC_DIR / "templates" / "static" / "detections"

# The checked-in stub for one group. `reset_detections_dir` writes exactly this,
# so the committed files and a freshly reset directory are byte-identical.
_STUB_TEMPLATE = """\
/**
 * Detector stub for the "{group}" extension group.
 *
 * Overwritten by a miner's submission at score time; this checked-in stub
 * keeps the bait page valid between rounds.
 */
function detect_{group}() {{
  return {{}};
}}

if (typeof window !== 'undefined') window.detect_{group} = detect_{group};
"""


def stub_source(group: str) -> str:
    """The clean stub for `group`."""
    return _STUB_TEMPLATE.format(group=group)


def _safe_target(detections_dir: Path, file_name: str) -> Path:
    """Resolve `file_name` inside `detections_dir`, or raise."""
    root = detections_dir.resolve()
    target = (root / file_name).resolve()
    if target.parent != root:
        raise ValueError(f"file_name '{file_name}' escapes the detections directory")
    return target


def stage_detection_files(
    miner_output: "MinerOutput", detections_dir: Path = DETECTIONS_DIR
) -> list[Path]:
    """Write the miner's files into the served tree.

    Returns the paths written, so the caller can restore them in a `finally`.
    If staging itself fails part-way, it restores what it already wrote before
    re-raising - the caller never got a list to restore from.
    """
    detections_dir.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []

    try:
        for commit_file in miner_output.commit_files:
            target = _safe_target(detections_dir, commit_file.file_name)
            staged.append(target)
            target.write_text(commit_file.content, encoding="utf-8")
    except Exception:
        restore_stubs(staged)
        raise

    logger.info(f"Staged {len(staged)} detection file(s) into {detections_dir}")
    return staged


def restore_stubs(staged: list[Path]) -> None:
    """Put clean stubs back. Never raises - it runs in a `finally`, where an
    exception would mask the real failure.

    Written from `stub_source`, not copied from a backup taken at staging:
    backups could capture another run's miner code, leaked into images built
    from a dev checkout, and one left by `run_round.py` run as root (the
    `docker exec` default) was unreadable by the app, so every later run
    failed to restore and left the miner's code served.
    """
    for target in staged:
        try:
            target.write_text(stub_source(target.stem), encoding="utf-8")
        except Exception as err:  # noqa: BLE001
            logger.warning(f"Could not restore stub for {target.name}: {err}")


def reset_detections_dir(
    groups: list[str], detections_dir: Path = DETECTIONS_DIR
) -> list[str]:
    """Bring the served directory back to exactly one clean stub per group.

    Run at startup. `restore_stubs` only undoes what a run staged, and only if
    that run reached its `finally`. This covers everything else: a crash or a
    kill mid-run, a submission copied in by hand, files for groups the pool no
    longer has. All of those have happened, and each leaves code in a directory
    that is both served to Chrome and tracked in git. Returns the names it
    changed, for the log.
    """
    detections_dir.mkdir(parents=True, exist_ok=True)
    changed: list[str] = []

    for group in groups:
        target = detections_dir / f"{group}.js"
        clean = stub_source(group)
        if not target.is_file() or target.read_text(encoding="utf-8") != clean:
            target.write_text(clean, encoding="utf-8")
            changed.append(target.name)

    wanted = {f"{group}.js" for group in groups}
    for path in sorted(detections_dir.iterdir()):
        if path.is_file() and path.suffix == ".js" and path.name not in wanted:
            path.unlink()
            changed.append(path.name)

    return changed


__all__ = [
    "DETECTIONS_DIR",
    "reset_detections_dir",
    "restore_stubs",
    "stage_detection_files",
    "stub_source",
]
