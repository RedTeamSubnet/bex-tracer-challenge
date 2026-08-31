from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import SettingsConfigDict

from api.core.constants import ENV_PREFIX_CHALLENGE

from ._base import BaseConfig


class BrowserConfig(BaseConfig):
    """Chrome for Testing launch settings."""

    chrome_bin: str = Field(default="/opt/chrome/browser/chrome")
    chromedriver_bin: str = Field(default="/opt/chrome/driver/chromedriver")
    extensions_dir: str = Field(default="/opt/extensions")
    scratch_dir: str = Field(
        default="/run/exc",
        description="tmpfs for per-round profiles and extension copies",
    )
    headless: bool = Field(
        default=True,
        description="False only for local dev under Xvfb; prod is always headless",
    )
    shm_fallback: bool = Field(
        default=False,
        description="Adds --disable-dev-shm-usage; prefer shm_size=2gb on the container",
    )
    page_load_timeout_sec: float = Field(default=30.0, gt=0)

    model_config = SettingsConfigDict(env_prefix=f"{ENV_PREFIX_CHALLENGE}BROWSER_")


class ChallengeConfig(BaseConfig):
    api_key: SecretStr = Field(default=SecretStr("challenge_api_key"))
    pool_path: str = Field(default="/app/extensions.yml")
    n_rounds: int = Field(
        default=20, ge=1, le=200, description="T - rounds per /score call"
    )
    # Fixed k: equal bounds. Randomising k made cardinality part of the
    # prediction; with a fixed k the miner knows |enabled| and can just rank the
    # pool and take the top k. MCC still floors random guessing at ~0 either way
    # (the 25% figure in design.md is about F1, which we do not use). Set these
    # to different values to go back to a random k - nothing else changes.
    k_min: int = Field(default=5, ge=1, description="min extensions enabled per round")
    k_max: int = Field(default=5, ge=1, description="max extensions enabled per round")
    max_parallel_rounds: int = Field(
        default=1,
        ge=1,
        le=16,
        description="P - concurrent browsers. Raise ONLY with measurement: "
        "shm_size and mem_limit are container-wide, so P browsers each get "
        "1/P of them. Measured 2026-08-30 with shm_size=2gb: P=1 completed "
        "4/4 rounds, P=2 3/4, P=4 1/4 - and failed rounds score 0, so an "
        "over-set P quietly deflates every miner's score",
    )
    settle_seconds: float = Field(
        default=6.0,
        gt=0,
        le=30,
        description="Wait after page load before sampling. Must cover the "
        "SLOWER of two clocks: the DOM settles by ~3.5s, but a blocker's "
        "declarativeNetRequest rulesets do not bite until ~5s. Measured by "
        "scripts/audit_activity.py",
    )
    script_budget_sec: float = Field(
        default=10.0, gt=0, description="Hard cap on the miner script"
    )
    submission_max_lines: int = Field(default=500, ge=1)
    submission_file_name: str = Field(default="solution.js")
    browser: BrowserConfig = Field(default_factory=BrowserConfig)

    @field_validator("submission_file_name", mode="after")
    @classmethod
    def _check_file_name(cls, val: str) -> str:
        if not val.endswith(".js"):
            raise ValueError("submission_file_name must end with '.js'")
        return val

    @model_validator(mode="after")
    def _check_k_range(self) -> "ChallengeConfig":
        if self.k_min > self.k_max:
            raise ValueError(f"k_min ({self.k_min}) > k_max ({self.k_max})")
        return self

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX_CHALLENGE)


__all__ = [
    "BrowserConfig",
    "ChallengeConfig",
]
