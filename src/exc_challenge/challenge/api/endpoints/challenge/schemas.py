from pydantic import BaseModel, Field, field_validator

from potato_util.generator import gen_random_string

from api.config import config

from ._pool import load_pool_groups
from .utils import stub_source


def _stub_examples() -> list[dict[str, str]]:
    """The OpenAPI example for `commit_files` - EVERY group file, not one.

    Swagger pre-fills the request body from this and `_check_commit_files`
    requires one file per group, so a single-file example is not unhelpful but
    INVALID: Try-it-out would 422 and read like a broken endpoint.

    Built from the stub template, never read from the served detections
    directory. This runs at import - before the startup reset - and a run
    killed mid-score leaves that miner's code there, which the public
    /openapi.json then published until the next restart.

    A missing pool degrades the example rather than breaking the import.
    """
    try:
        _groups = list(load_pool_groups())
    except Exception:  # noqa: BLE001 - an example must never break startup
        _groups = ["detect"]
    return [
        {"file_name": f"{_group}.js", "content": stub_source(_group)}
        for _group in _groups
    ]


class MinerInput(BaseModel):
    random_val: str | None = Field(
        default_factory=gen_random_string,
        title="Random Value",
        description="Random value to prevent caching.",
        examples=["a1b2c3d4e5f6g7h8"],
    )
    extension_names: list[str] = Field(
        default_factory=list,
        title="Extension Names",
        description="The published extension pool, by name. A random subset is "
        "enabled per round; return one boolean per name. Store ids are not "
        "published and would not help: extensions are loaded unpacked with no "
        "`key`, so Chrome assigns each one a different id every round.",
        examples=[["Grammarly", "Dark Reader"]],
    )
    groups: dict[str, list[str]] = Field(
        default_factory=dict,
        title="Extension Groups",
        description="group name -> extension names in that group. Submit one "
        "file per group, named `<group>.js`, each defining "
        "`window.detect_<group>`.",
        examples=[{"writing": ["Grammarly", "LanguageTool"]}],
    )


class CommitFilePM(BaseModel):
    file_name: str = Field(
        ...,
        min_length=4,
        max_length=64,
        title="File Name",
        description="`<group>.js`, one per group published by GET /task.",
    )
    content: str = Field(
        ...,
        min_length=2,
        title="File Content",
        description="Content of the file as a string.",
    )


class MinerOutput(BaseModel):
    commit_files: list[CommitFilePM] = Field(
        ...,
        title="Commit Files",
        description="One file per group published by GET /task, named "
        "`<group>.js` and defining `window.detect_<group>`. ALL groups are "
        "required - a missing or unexpected file name is rejected.",
        examples=[_stub_examples()],
    )

    @field_validator("commit_files", mode="after")
    @classmethod
    def _check_commit_files(cls, val: list[CommitFilePM]) -> list[CommitFilePM]:
        _max_lines: int = config.challenge.submission_max_lines
        _expected_names = {f"{_group}.js" for _group in load_pool_groups()}

        _file_names = [_miner_file_pm.file_name for _miner_file_pm in val]
        if len(_file_names) != len(_expected_names) or set(_file_names) != _expected_names:
            raise ValueError(
                f"expected exactly one file per group ({sorted(_expected_names)}), "
                f"got {sorted(_file_names)}!"
            )

        for _miner_file_pm in val:
            _content_lines = _miner_file_pm.content.splitlines()
            if len(_content_lines) > _max_lines:
                raise ValueError(
                    f"`{_miner_file_pm.file_name}` file contains too many lines, should be <= {_max_lines} lines!"
                )

        return val


class RoundReportPM(BaseModel):
    """One round, as reported by `RoundRecord.as_public_dict()`."""

    index: int = Field(..., description="round number within the run")
    status: str = Field(..., description="completed, failed or infra_failed")
    n_enabled: int = Field(..., description="how many extensions were enabled")
    score: float = Field(..., description="this round's clamped MCC")
    failed: bool = Field(..., description="whether the round raised")
    duration_sec: float | None = Field(None, description="wall time for the round")


class RunReportPM(BaseModel):
    """Outcome of the most recent scoring run.

    Deliberately carries NO ground truth - no enabled set, no per-extension
    labels, no error text (browser errors name the extensions they failed to
    load, which is the round's answer key). Declaring the shape here also means
    a field added to `report()` later cannot reach the response without being
    added to this model on purpose.
    """

    pool_size: int
    n_rounds: int
    n_completed: int
    n_scored: int
    score: float
    rounds: list[RoundReportPM]


__all__ = [
    "MinerInput",
    "CommitFilePM",
    "MinerOutput",
    "RoundReportPM",
    "RunReportPM",
]
