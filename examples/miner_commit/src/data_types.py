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
    extension_names: list[str] = Field(
        default_factory=list,
        title="Extension Names",
        description="The whole published pool, by name. A random subset is "
        "enabled each round; you return one boolean per name. Store ids are "
        "NOT published and would not help - extensions load unpacked with no "
        "`key`, so Chrome gives each one a different id every round.",
        examples=[["Dark Reader", "Privacy Badger"]],
    )
    groups: dict[str, list[str]] = Field(
        default_factory=dict,
        title="Extension Groups",
        description="group name -> the extension names that group owns. Submit "
        "exactly one file per group, named `<group>.js`, each defining "
        "`window.detect_<group>`. This is the source of truth for your file "
        "names - never hardcode them.",
        examples=[{"blockers": ["uBlock Origin Lite", "Privacy Badger"]}],
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

    @field_validator("commit_files", mode="after")
    @classmethod
    def _check_commit_files(cls, val: list[CommitFilePM]) -> list[CommitFilePM]:
        for _miner_file_pm in val:
            _content_lines = _miner_file_pm.content.splitlines()
            if len(_content_lines) > 500:
                raise ValueError(
                    f"`{_miner_file_pm.file_name}` file contains too many lines, should be <= 500 lines!"
                )

        return val


__all__ = [
    "MinerInput",
    "CommitFilePM",
    "MinerOutput",
]
