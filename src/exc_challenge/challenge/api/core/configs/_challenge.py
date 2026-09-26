from pydantic import Field, SecretStr
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
    pool_path: str = Field(default="/app/rest-exc-challenge/extensions.yml")
    # These defaults ARE the production values: prod starts the container without
    # the compose-mounted config file, so anything set only in
    # volumes/configs/.../challenge.yml silently does not apply there.
    #
    # 6 rounds of 12 over an 89-extension pool: 72 draws, so coverage weighting
    # enables most of the pool once per run. One run still moves with which
    # extensions are drawn, which the validator averages out over runs.
    n_rounds: int = Field(
        default=6, ge=1, le=200, description="T - rounds per /score call"
    )
    # Fixed across the run: every round enables exactly this many extensions,
    # drawn at random. The miner therefore knows |enabled| and can rank the pool
    # and take the top k, and a miner predicting exactly k positives has
    # FP == FN by construction. MCC still floors random guessing at ~0 (the 25%
    # figure in design.md is about F1, which we do not use).
    # INVARIANT: must be < the pool size, or build_round_schedule() raises -
    # with everything enabled there is no negative class and even a perfect
    # prediction scores 0.0.
    k: int = Field(default=12, ge=1, description="extensions enabled per round")
    coverage_bias: float = Field(
        default=2.0,
        ge=1.0,
        le=10.0,
        description="Weights each round's draw toward the least-used "
        "extensions: weight = coverage_bias ** -times_used. 1.0 is uniform, "
        "which leaves ~2.7 of 25 never enabled in a 10-round run - those can "
        "only cost a miner, never earn them anything. Higher is more even but "
        "more inferable; a used extension is never excluded, so no round "
        "becomes deducible. Raise above ~3 only once egress from the bait "
        "page is closed - see build_round_schedule.",
    )
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
    submission_max_lines: int = Field(default=750, ge=1)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX_CHALLENGE)


__all__ = [
    "BrowserConfig",
    "ChallengeConfig",
]
