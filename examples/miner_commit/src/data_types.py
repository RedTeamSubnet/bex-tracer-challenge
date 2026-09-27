from typing import ClassVar

from pydantic import BaseModel, Field, field_validator


class MinerInput(BaseModel):
    """What `GET /task` sends you. Mirror of the challenge's own schema.

    `extension_names` and `groups` are the whole task. Leave them out and
    pydantic drops them silently - you never see what you were asked, and the
    only way to name your files correctly is to guess.
    """

    random_val: str | None = Field(
        default=None,
        min_length=4,
        max_length=64,
        title="Random Value",
        description="Random value to prevent caching.",
        examples=["a1b2c3d4e5f6g7h8"],
    )


class CommitFilePM(BaseModel):
    file_name: str = Field(
        ...,
        min_length=4,
        max_length=64,
        title="File Name",
        description="Name of the file.",
        examples=["blockers.js"],
    )
    content: str = Field(
        ...,
        min_length=2,
        title="File Content",
        description="Content of the file as a string.",
        examples=["console.log('Challenge accepted!');"],
    )


class MinerOutput(BaseModel):
    commit_files: list[CommitFilePM] = Field(
        ...,
        title="Commit Files",
        description="List of Commit files for the challenge.",
    )

    # Mirror of the challenge's `submission_max_lines`. Keep them equal: set
    # lower and this rejects submissions the challenge would accept; set higher
    # and a file passes here only to 422 remotely.
    MAX_LINES: ClassVar[int] = 750
    # Mirror of `submission_max_bytes`, same rule.
    MAX_BYTES: ClassVar[int] = 262144

    @field_validator("commit_files", mode="after")
    @classmethod
    def _check_commit_files(cls, val: list[CommitFilePM]) -> list[CommitFilePM]:
        for _miner_file_pm in val:
            _content_lines = _miner_file_pm.content.splitlines()
            if len(_content_lines) > MinerOutput.MAX_LINES:
                raise ValueError(
                    f"`{_miner_file_pm.file_name}` file contains too many lines, "
                    f"should be <= {MinerOutput.MAX_LINES} lines!"
                )
            _size = len(_miner_file_pm.content.encode("utf-8"))
            if _size > MinerOutput.MAX_BYTES:
                raise ValueError(
                    f"`{_miner_file_pm.file_name}` is {_size} bytes, "
                    f"should be <= {MinerOutput.MAX_BYTES} bytes!"
                )

        return val


__all__ = [
    "MinerInput",
    "CommitFilePM",
    "MinerOutput",
]
