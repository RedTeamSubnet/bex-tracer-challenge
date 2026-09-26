import sys
import logging
import pathlib
from pathlib import Path

from fastapi import FastAPI, Body, HTTPException
from data_types import MinerInput, MinerOutput, CommitFilePM

logger = logging.getLogger(__name__)
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S %z",
    format="[%(asctime)s | %(levelname)s | %(filename)s:%(lineno)d]: %(message)s",
)


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/solve", response_model=MinerOutput)
def solve(miner_input: MinerInput = Body(...)) -> MinerOutput:

    logger.info("Retrieving commit files...")
    _miner_output: MinerOutput
    try:
        _src_dir = pathlib.Path(__file__).parent.resolve()
        _commit_dir = _src_dir / "commit"

        # Build the file list from the TASK, not from whatever happens to be
        # on disk. The challenge requires exactly one file per group and
        # rejects the whole submission on a name it did not ask for - so a
        # leftover file from an older pool fails the run with a 422 that says
        # nothing about which file was wrong. Driving off `groups` means a pool
        # change shows up here as a missing detector, not a remote rejection.
        _groups = list(miner_input.groups or [])
        if not _groups:
            raise HTTPException(
                status_code=400,
                detail="miner_input.groups is empty - cannot name the files. "
                "Call GET /task on the challenge and pass what it returns.",
            )

        _commit_files: list[CommitFilePM] = []
        for _group in _groups:
            _commit_path = _commit_dir / f"{_group}.js"
            if _commit_path.is_file():
                _content = _commit_path.read_text()
            else:
                # A stub keeps the submission VALID while you work on the
                # others. Every group must be present, so an empty detector
                # scores that group false rather than failing the run.
                logger.warning(f"no detector for '{_group}', submitting a stub")
                _content = (
                    f"window.detect_{_group} = async () => ({{}});"
                )
            _commit_files.append(
                CommitFilePM(file_name=f"{_group}.js", content=_content)
            )

        _miner_output = MinerOutput(commit_files=_commit_files)
        logger.info("Successfully retrieved commit files.")
    except HTTPException:
        raise
    except Exception as err:
        logger.error(f"Failed to retrieve commit files: {str(err)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve commit files.")

    return _miner_output


__all__ = ["app"]
