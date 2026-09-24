"""The miner's files are written into a directory the app serves publicly, so
the two things that matter are: none can escape that directory, and none
outlives the run that submitted it.

The mechanism under test (`stage_detection_files` / `restore_stubs` /
`_safe_target`) is filename-agnostic - it does not know about groups - so a
single `blockers.js`-named fixture exercises it fully without needing all 7
group files."""

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "src/exc_challenge/challenge")
)

from api.endpoints.challenge.utils import (  # noqa: E402
    _safe_target,
    reset_detections_dir,
    restore_stubs,
    stage_detection_files,
    stub_source,
)


class _File:
    def __init__(self, file_name: str, content: str) -> None:
        self.file_name = file_name
        self.content = content


class _Output:
    def __init__(self, *files: _File) -> None:
        self.commit_files = list(files)


@pytest.fixture
def detections(tmp_path):
    """A detections dir holding the checked-in stub."""
    d = tmp_path / "detections"
    d.mkdir()
    (d / "blockers.js").write_text("// stub\n", encoding="utf-8")
    return d


def test_staging_replaces_the_stub_with_the_submission(detections):
    stage_detection_files(_Output(_File("blockers.js", "// miner\n")), detections)
    assert (detections / "blockers.js").read_text() == "// miner\n"


def test_restore_puts_the_stub_back(detections):
    staged = stage_detection_files(
        _Output(_File("blockers.js", "// miner\n")), detections
    )
    restore_stubs(staged, detections)
    assert (detections / "blockers.js").read_text() == "// stub\n"


def test_a_second_run_still_restores_the_original_stub(detections):
    """The backup is written once per target. Without that, run 2 would snapshot
    run 1's miner code and 'restore' one miner's submission over another's."""
    for miner in ("// miner A\n", "// miner B\n"):
        staged = stage_detection_files(_Output(_File("blockers.js", miner)), detections)
        restore_stubs(staged, detections)
    assert (detections / "blockers.js").read_text() == "// stub\n"


def test_restore_runs_after_a_failed_round(detections):
    """restore_stubs lives in a `finally`; it must not raise even if the file
    was removed underneath it, or it would mask the real failure."""
    staged = stage_detection_files(
        _Output(_File("blockers.js", "// miner\n")), detections
    )
    (detections / "blockers.js").unlink()
    restore_stubs(staged, detections)  # must not raise
    assert (detections / "blockers.js").read_text() == "// stub\n"


@pytest.mark.parametrize(
    "hostile",
    [
        "../config.py",
        "../../api/config.py",
        "sub/nested.js",
        "/etc/passwd",
    ],
)
def test_a_file_name_cannot_escape_the_detections_directory(detections, hostile):
    """`file_name` comes from the request. Joining it onto a path unchecked is
    an arbitrary file write into the running app."""
    with pytest.raises(ValueError, match="escapes"):
        _safe_target(detections, hostile)


def test_a_plain_file_name_resolves_inside(detections):
    assert _safe_target(detections, "blockers.js").parent == detections.resolve()


# -- startup reset -------------------------------------------------------------


def test_reset_overwrites_a_solution_left_in_a_group_file(tmp_path):
    """The 2026-09-21 incident: working detectors sat in the served, git-tracked
    directory with no `.stub` beside them, so `restore_stubs` could not help."""
    (tmp_path / "ad_blockers.js").write_text("window.detect_ad_blockers = () => SOLUTION")
    changed = reset_detections_dir(["ad_blockers"], tmp_path)
    assert (tmp_path / "ad_blockers.js").read_text() == stub_source("ad_blockers")
    assert changed == ["ad_blockers.js"]


def test_reset_creates_missing_stubs(tmp_path):
    reset_detections_dir(["ad_blockers", "vpn_proxy"], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ad_blockers.js", "vpn_proxy.js"]


def test_reset_removes_old_groups_and_stub_backups(tmp_path):
    (tmp_path / "canvas_api.js").write_text("old group")
    (tmp_path / "canvas_api.js.stub").write_text("old backup")
    (tmp_path / "ad_blockers.js.stub").write_text("backup that may hold miner code")
    reset_detections_dir(["ad_blockers"], tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ad_blockers.js"]


def test_reset_leaves_a_clean_directory_untouched(tmp_path):
    """Startup must not rewrite committed files that are already correct."""
    reset_detections_dir(["ad_blockers"], tmp_path)
    assert reset_detections_dir(["ad_blockers"], tmp_path) == []


def test_reset_ignores_files_that_are_not_detectors(tmp_path):
    (tmp_path / "README.txt").write_text("notes")
    reset_detections_dir(["ad_blockers"], tmp_path)
    assert (tmp_path / "README.txt").exists()


def test_committed_stubs_match_what_startup_writes():
    """Otherwise every startup in a dev checkout would show up as a git diff."""
    from api.endpoints.challenge.utils import DETECTIONS_DIR

    for path in DETECTIONS_DIR.glob("*.js"):
        assert path.read_text() == stub_source(path.stem), path.name
