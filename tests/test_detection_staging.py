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
    restore_stubs,
    stage_detection_files,
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
