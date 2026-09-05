"""Orchestration for the extension-classification challenge.

Builds the round schedule, drives each round through a browser, records what the
miner predicted, and returns the run score. It never computes the metric itself -
that lives in `_payload_manager.py`.
"""

import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from pydantic import validate_call

from api.config import config
from api.logger import logger

from . import utils as ch_utils
from ._browser import BrowserSettings, BrowserInfraError, run_round
from ._payload_manager import PayloadManager, RoundRecord
from ._pool import load_pool_groups, load_pool_ids
from .schemas import MinerInput, MinerOutput

# Infra failures no longer deflate the score - `PayloadManager.scored_rounds()`
# drops them from the denominator, so a lost browser costs the miner nothing.
# What they still cost is SAMPLE SIZE. Past this share, too few rounds actually
# ran for the average to mean anything, and returning it anyway would hand the
# validator a number that looks authoritative but is not.
#
# Only `BrowserInfraError` counts here. A miner's own failure is evidence about
# the miner: it scores 0, stays in the denominator, and is not this guard's
# business.
_MAX_SETUP_FAILURE_RATIO = 0.2


def get_task() -> MinerInput:
    return MinerInput(
        extension_ids=list(load_pool_ids()),
        groups={_g: list(_ids) for _g, _ids in load_pool_groups().items()},
    )


def _bait_page_url() -> str:
    """The bait page is served by this very app, mounted at `/_web`.

    Chrome runs in the same container, so it reaches the app over loopback
    regardless of what `bind_host` is set to.
    """
    return f"http://127.0.0.1:{config.api.port}/_web/index.html"


class RoundResult(NamedTuple):
    """What one worker thread came back with."""

    index: int
    predicted: dict[str, bool] | None
    error: Exception | None
    elapsed_sec: float


def _run_one_round(
    round_record: RoundRecord,
    *,
    pool: list[str],
    groups: Mapping[str, Sequence[str]],
    page_url: str,
    settings: BrowserSettings,
    request_id: str,
) -> RoundResult:
    """One round, on a worker thread. Returns rather than raises, so a single
    bad round cannot take the pool of workers down with it."""
    _started_at = time.monotonic()
    try:
        _predicted = run_round(
            round_record.enabled,
            pool=pool,
            groups=groups,
            page_url=page_url,
            settings=settings,
            settle_seconds=config.challenge.settle_seconds,
            script_budget_sec=config.challenge.script_budget_sec,
            round_tag=f"{request_id}-{round_record.index}",
        )
    except Exception as err:  # one bad round must not abort the run
        return RoundResult(round_record.index, None, err, time.monotonic() - _started_at)
    return RoundResult(round_record.index, _predicted, None, time.monotonic() - _started_at)


def _run_all_rounds(
    rounds: list[RoundRecord],
    *,
    pool: list[str],
    groups: Mapping[str, Sequence[str]],
    page_url: str,
    settings: BrowserSettings,
    request_id: str,
) -> list[RoundResult]:
    """Drive every round, in parallel, collecting outcomes rather than raising.

    Rounds are independent: each gets its own scratch root and profile, and the
    process sweeper matches on that path so one round's teardown cannot touch
    another's browser. Peak RAM is roughly 1GB per concurrent Chrome.
    """
    _workers = max(1, min(config.challenge.max_parallel_rounds, len(rounds)))
    logger.info(f"[{request_id}] - Using {_workers} parallel browser(s).")

    with ThreadPoolExecutor(max_workers=_workers) as _executor:
        return list(
            _executor.map(
                lambda rec: _run_one_round(
                    rec,
                    pool=pool,
                    groups=groups,
                    page_url=page_url,
                    settings=settings,
                    request_id=request_id,
                ),
                rounds,
            )
        )


def _record_all(
    payload_manager: PayloadManager,
    results: list[RoundResult],
    request_id: str,
) -> tuple[int, str | None]:
    """Score every round on this thread. Returns (setup failures, last error kind).

    `executor.map` yields in the order of the input rather than completion, so
    recording here is already index-ordered and the report is deterministic.

    The full exception text can name extensions, which is this round's answer
    key, so it goes to the log and nowhere else. `RoundRecord` and the returned
    "last error" keep only the exception class name.
    """
    _setup_failures = 0
    _last_error: str | None = None

    for _result in results:
        _kind: str | None = None
        _infra = False
        if _result.error is not None:
            _kind = type(_result.error).__name__
            _last_error = _kind
            if isinstance(_result.error, BrowserInfraError):
                # The browser failed us - staging, launch, navigation or a dead
                # renderer. Not evidence about the miner, so it is excluded from
                # the score denominator (see PayloadManager.scored_rounds).
                _infra = True
                _setup_failures += 1
                _reason = "lost the browser"
            else:
                _reason = "failed"
            logger.warning(
                f"[{request_id}] - Round {_result.index} {_reason}: {_result.error}"
            )

        payload_manager.record(
            _result.index,
            _result.predicted,
            error=_kind,
            duration_sec=round(_result.elapsed_sec, 2),
            infra=_infra,
        )

    return _setup_failures, _last_error


@validate_call
def score(request_id: str, miner_output: MinerOutput) -> float:

    _pool: list[str] = list(load_pool_ids())
    _challenge_config = config.challenge

    _payload_manager = PayloadManager(pool=_pool)
    _payload_manager.build_schedule(
        n_rounds=_challenge_config.n_rounds,
        k=_challenge_config.k,
    )

    # `BrowserSettings` mirrors `BrowserConfig` field for field; an unexpected
    # kwarg here means they have drifted, and that should fail loudly.
    _browser_settings = BrowserSettings(**_challenge_config.browser.declared_dump())

    logger.info(
        f"[{request_id}] - Running {len(_payload_manager.rounds)} round(s) "
        f"over a pool of {len(_pool)}..."
    )

    # The miner's files are served as static/detections/<group>.js and loaded
    # by the bait page itself, matching ab_sniffer and ada_detection. Every
    # round in this run uses the same submission, so it is staged once, and
    # restored in the `finally` so a miner's code never outlives the run that
    # sent it.
    _staged = ch_utils.stage_detection_files(miner_output)
    try:
        _results = _run_all_rounds(
            _payload_manager.rounds,
            pool=_pool,
            groups=load_pool_groups(),
            page_url=_bait_page_url(),
            settings=_browser_settings,
            request_id=request_id,
        )
        _setup_failures, _last_error = _record_all(
            _payload_manager, _results, request_id
        )

        # Not "the score would be unfairly low" - infra failures are already
        # out of the denominator. The problem is that too few rounds survived
        # for the mean to say anything, and a thin average is indistinguishable
        # from a solid one once it reaches the validator as a bare float.
        _n_rounds = len(_payload_manager.rounds)
        if _setup_failures > _MAX_SETUP_FAILURE_RATIO * _n_rounds:
            raise RuntimeError(
                f"{_setup_failures} of {_n_rounds} round(s) lost the browser, "
                f"leaving too few scored rounds to average. If max_parallel_rounds "
                f"({_challenge_config.max_parallel_rounds}) was raised, lower it: "
                f"shm_size and mem_limit are shared across concurrent browsers. "
                # The class name only. The full text names extensions, and this
                # exception is logged with a traceback by router.py.
                f"Last failure was {_last_error}; see the log for details."
            )

        _score: float = _payload_manager.calculate_score()
        _report = _payload_manager.report()
        logger.info(
            f"[{request_id}] - Scored {_score:.4f} "
            f"({_report['n_completed']}/{_report['n_rounds']} round(s) completed, "
            f"averaged over {_report['n_scored']})."
        )
        return _score
    finally:
        ch_utils.restore_stubs(_staged)


__all__ = [
    "get_task",
    "score",
]
