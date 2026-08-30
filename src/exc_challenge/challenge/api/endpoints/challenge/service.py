"""Orchestration for the extension-classification challenge.

Builds the round schedule, drives each round through a browser, records what the
miner predicted, and returns the run score. It never computes the metric itself -
that lives in `_payload_manager.py`.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from pydantic import validate_call

from api.config import config
from api.logger import logger

from ._browser import BrowserSettings, BrowserInfraError, run_round
from ._payload_manager import PayloadManager, RoundRecord
from ._pool import load_pool_ids
from .schemas import MinerInput, MinerOutput

# Failed rounds score 0 and drag the mean down, so a run with many broken
# rounds returns a low score that looks like a bad miner. Past this share of
# browsers failing to start, the number is not about the miner any more.
_MAX_SETUP_FAILURE_RATIO = 0.2


def get_task() -> MinerInput:
    return MinerInput(extension_ids=list(load_pool_ids()))


def _bait_page_url() -> str:
    """The bait page is served by this very app, mounted at `/_web`.

    Chrome runs in the same container, so it reaches the app over loopback
    regardless of what `bind_host` is set to.
    """
    return f"http://127.0.0.1:{config.api.port}/_web/index.html"


def _run_one_round(
    round_record: RoundRecord,
    *,
    miner_js: str,
    pool: list[str],
    page_url: str,
    settings: BrowserSettings,
    request_id: str,
) -> tuple[int, dict[str, bool] | None, Exception | None, float]:
    """One round, on a worker thread. Returns rather than raises, so a single
    bad round cannot take the pool of workers down with it."""
    _started_at = time.monotonic()
    try:
        _predicted = run_round(
            round_record.enabled,
            miner_js,
            pool=pool,
            page_url=page_url,
            settings=settings,
            settle_seconds=config.challenge.settle_seconds,
            script_budget_sec=config.challenge.script_budget_sec,
            round_tag=f"{request_id}-{round_record.index}",
        )
    except Exception as err:  # one bad round must not abort the run
        return round_record.index, None, err, time.monotonic() - _started_at
    return round_record.index, _predicted, None, time.monotonic() - _started_at


@validate_call
def score(request_id: str, miner_output: MinerOutput) -> float:

    _pool: list[str] = list(load_pool_ids())
    _challenge_config = config.challenge

    _payload_manager = PayloadManager(pool=_pool)
    _payload_manager.build_schedule(
        n_rounds=_challenge_config.n_rounds,
        k_min=_challenge_config.k_min,
        k_max=_challenge_config.k_max,
    )

    # The schema guarantees exactly one file, named `submission_file_name`.
    _miner_js: str = miner_output.commit_files[0].content
    # `BrowserSettings` mirrors `BrowserConfig` field for field; an unexpected
    # kwarg here means they have drifted, and that should fail loudly.
    _browser_settings = BrowserSettings(**_challenge_config.browser.declared_dump())
    _page_url: str = _bait_page_url()

    logger.info(
        f"[{request_id}] - Running {len(_payload_manager.rounds)} round(s) "
        f"over a pool of {len(_pool)}..."
    )

    # Rounds are independent: each gets its own scratch root and profile, and
    # the process sweeper matches on that path so one round's teardown cannot
    # touch another's browser. Peak RAM is roughly 1GB per concurrent Chrome.
    _workers = max(
        1, min(_challenge_config.max_parallel_rounds, len(_payload_manager.rounds))
    )
    logger.info(f"[{request_id}] - Using {_workers} parallel browser(s).")

    with ThreadPoolExecutor(max_workers=_workers) as _executor:
        _results = list(
            _executor.map(
                lambda rec: _run_one_round(
                    rec,
                    miner_js=_miner_js,
                    pool=_pool,
                    page_url=_page_url,
                    settings=_browser_settings,
                    request_id=request_id,
                ),
                _payload_manager.rounds,
            )
        )

    # Recorded on this thread, in index order, so the report is deterministic
    # regardless of the order the workers happened to finish in.
    _setup_failures: int = 0
    _error: str | None = None
    for _index, _predicted, _err, _elapsed in sorted(_results, key=lambda r: r[0]):
        if _err is not None:
            _error = str(_err)
            if isinstance(_err, BrowserInfraError):
                # Chrome could not start at all - infrastructure, not the miner.
                _setup_failures += 1
                logger.warning(
                    f"[{request_id}] - Round {_index} could not start: {_err}"
                )
            else:
                logger.warning(f"[{request_id}] - Round {_index} failed: {_err}")

        _payload_manager.record(
            _index,
            _predicted,
            error=_error if _err is not None else None,
            duration_sec=round(_elapsed, 2),
        )

    # A low score from "the miner guessed badly" and a low score from "the
    # browsers would not start" are indistinguishable to the validator, and the
    # second one is our bug. Fail the request so it cannot be read as a score.
    _n_rounds = len(_payload_manager.rounds)
    if _setup_failures > _MAX_SETUP_FAILURE_RATIO * _n_rounds:
        raise RuntimeError(
            f"{_setup_failures} of {_n_rounds} round(s) failed to start a browser, "
            f"so the score would not be about the miner. If max_parallel_rounds "
            f"({_challenge_config.max_parallel_rounds}) was raised, lower it: "
            f"shm_size and mem_limit are shared across concurrent browsers. "
            f"Last error: {_error}"
        )

    _score: float = _payload_manager.calculate_score()
    _report = _payload_manager.report()
    logger.info(
        f"[{request_id}] - Scored {_score:.4f} "
        f"({_report['n_completed']}/{_report['n_rounds']} round(s) completed)."
    )

    return _score


__all__ = [
    "get_task",
    "score",
]
