from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from potato_util.generator import gen_random_string

from api.config import config

# The checked-in stub, used as the OpenAPI example. Swagger pre-fills request
# bodies from these, so a placeholder like "console.log('hi')" means anyone
# who hits Try-it-out scores 0.0 with a confusing "no window.detect_extensions".
_STUB_PATH = (
    Path(__file__).resolve().parents[3]
    / "templates"
    / "static"
    / "detections"
    / config.challenge.submission_file_name
)
try:
    _STUB_CONTENT = _STUB_PATH.read_text(encoding="utf-8")
except OSError:  # pragma: no cover - the app still runs without an example
    _STUB_CONTENT = "window.detect_extensions = async () => ({});"


class MinerInput(BaseModel):
    random_val: str | None = Field(
        default_factory=gen_random_string,
        title="Random Value",
        description="Random value to prevent caching.",
        examples=["a1b2c3d4e5f6g7h8"],
    )
    extension_ids: list[str] = Field(
        default_factory=list,
        title="Extension IDs",
        description="The published extension pool. A random subset is enabled "
        "per round; return one boolean per id.",
        examples=[["kbfnbcaeplbcioakkpcpgfkobkghlhen"]],
    )


class CommitFilePM(BaseModel):
    file_name: str = Field(
        ...,
        min_length=4,
        max_length=64,
        title="File Name",
        description="Name of the file.",
        examples=["solution.js"],
    )
    content: str = Field(
        ...,
        min_length=2,
        title="File Content",
        description="Content of the file as a string.",
        examples=[_STUB_CONTENT],
    )


class MinerOutput(BaseModel):
    commit_files: list[CommitFilePM] = Field(
        ...,
        title="Commit Files",
        description="List of Commit files for the challenge.",
    )

    @field_validator("commit_files", mode="after")
    @classmethod
    def _check_commit_files(cls, val: list[CommitFilePM]) -> list[CommitFilePM]:
        _expected_name: str = config.challenge.submission_file_name
        _max_lines: int = config.challenge.submission_max_lines

        _file_names = [_miner_file_pm.file_name for _miner_file_pm in val]
        if _file_names != [_expected_name]:
            raise ValueError(
                f"expected exactly one file named `{_expected_name}`, got {_file_names}!"
            )

        for _miner_file_pm in val:
            _content_lines = _miner_file_pm.content.splitlines()
            if len(_content_lines) > _max_lines:
                raise ValueError(
                    f"`{_miner_file_pm.file_name}` file contains too many lines, should be <= {_max_lines} lines!"
                )

        return val


__all__ = [
    "MinerInput",
    "CommitFilePM",
    "MinerOutput",
]
