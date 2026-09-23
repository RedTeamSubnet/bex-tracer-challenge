"""Orchestration for the extension-classification challenge.

Builds the round schedule, drives each round through a browser, records what the
miner predicted, and returns the run score. It never computes the metric itself -
that lives in `_payload_manager.py`.
"""

import secrets
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple

from pydantic import validate_call

from api.config import config
from api.logger import logger

from . import utils as ch_utils
from ._browser import BrowserSettings, BrowserInfraError, run_round
from ._payload_manager import PayloadManager, RoundRecord
from ._pool import load_name_to_id, load_pool_groups, load_pool_names
from .schemas import MinerInput, MinerOutput

# Infra failures are already out of the denominator, so they cost sample size,
# not score. Past this share too few rounds ran for the average to mean
# anything. Counts `BrowserInfraError` only - a miner's own failure is not this
# guard's business.
_MAX_SETUP_FAILURE_RATIO = 0.2


def get_task() -> MinerInput:
    """The published pool, by name.

    Ids are deliberately absent. They would also be useless: extensions load
    unpacked without `key`, so Chrome derives a fresh id from the staging path
    every round - see `_browser.derive_unpacked_id`.
    """
    return MinerInput(
        extension_names=list(load_pool_names()),
        groups={_g: list(_names) for _g, _names in load_pool_groups().items()},
    )


# Outcome of the most recent run, for GET /results. Kept here rather than on
# the manager because a manager is per-run and is discarded with it. Writes are
# serialised by the single-flight lock in router.py, so at most one run can be
# setting this.
_last_report: dict[str, Any] | None = None


def get_results() -> dict[str, Any] | None:
    """The last run's report, or None if nothing has been scored yet."""
    return _last_report


# Not 127.0.0.1: extensions often skip localhost (Random User-Agent ships a
# default blacklist of exactly `localhost` and `127.0.0.1`), which made them
# look undetectable. Chrome maps this name back to loopback itself - see
# `_browser.bait_page_args`. Internal to the container; nothing to configure.
_BAIT_HOST = "baitpage.test"


def bait_page_url() -> str:
    """The bait page is served by this very app, at `/_web`.

    Chrome runs in the same container, so it reaches the app over loopback
    regardless of what `bind_host` is set to.

    No trailing slash: the page's asset paths are relative, and they only
    resolve onto the `/static` mount when the browser's base URL is `/`.
    """
    return f"http://{_BAIT_HOST}:{config.api.port}/_web"


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
    id_map: Mapping[str, str],
    page_url: str,
    settings: BrowserSettings,
    request_id: str,
    path_nonce: str,
) -> RoundResult:
    """One round, on a worker thread. Returns rather than raises, so a single
    bad round cannot take the pool of workers down with it.

    `path_nonce` - not `request_id` - names the staging directory. See
    `_run_all_rounds` for why that distinction is load-bearing."""
    _started_at = time.monotonic()
    # The enabled set is this round's answer key, so it is never logged above
    # DEBUG and never leaves the container. At DEBUG it is the only way to tell
    # which extension broke a browser - `level.base: INFO` in logger.yml keeps
    # it off in production, and an operator diagnosing a failure turns it on.
    logger.debug(
        f"[{request_id}] - Round {round_record.index} enabling "
        f"{sorted(round_record.enabled)}"
    )
    try:
        _predicted = run_round(
            round_record.enabled,
            pool=pool,
            groups=groups,
            id_map=id_map,
            page_url=page_url,
            settings=settings,
            settle_seconds=config.challenge.settle_seconds,
            script_budget_sec=config.challenge.script_budget_sec,
            round_tag=f"{path_nonce}-{round_record.index}",
        )
    except Exception as err:  # one bad round must not abort the run
        return RoundResult(round_record.index, None, err, time.monotonic() - _started_at)
    return RoundResult(round_record.index, _predicted, None, time.monotonic() - _started_at)


def _run_all_rounds(
    rounds: list[RoundRecord],
    *,
    pool: list[str],
    groups: Mapping[str, Sequence[str]],
    id_map: Mapping[str, str],
    page_url: str,
    settings: BrowserSettings,
    request_id: str,
) -> list[RoundResult]:
    """Drive every round, in parallel, collecting outcomes rather than raising.

    Rounds are independent: each gets its own scratch root and profile, and the
    process sweeper matches on that path so one round's teardown cannot touch
    another's browser. Peak RAM is roughly 1GB per concurrent Chrome.
    """
    # The staging path decides the id Chrome assigns each extension, so it has
    # to be unguessable. `request_id` is NOT safe to use here: the logging
    # middleware honours a client-supplied `X-Request-ID` header, so whoever
    # calls /score could choose it, reconstruct
    # `<scratch_dir>/round-<request_id>-<index>/ext/<store id>`, run the same
    # derivation `_browser.derive_unpacked_id` uses, and probe
    # web_accessible_resources by the resulting id - which is exactly the
    # lookup the per-round rotation exists to kill. `scratch_dir` and the store
    # ids are both public, so the tag is the only secret in that path.
    #
    # This nonce is generated here, never logged above DEBUG, and never
    # reaches a response body, a header or the bait page.
    _path_nonce = secrets.token_hex(8)
    logger.debug(f"[{request_id}] - staging nonce {_path_nonce}")

    _workers = max(1, min(config.challenge.max_parallel_rounds, len(rounds)))
    logger.info(f"[{request_id}] - Using {_workers} parallel browser(s).")

    with ThreadPoolExecutor(max_workers=_workers) as _executor:
        return list(
            _executor.map(
                lambda rec: _run_one_round(
                    rec,
                    pool=pool,
                    groups=groups,
                    id_map=id_map,
                    page_url=page_url,
                    settings=settings,
                    request_id=request_id,
                    path_nonce=_path_nonce,
                ),
                rounds,
            )
        )


def _record_all(
    payload_manager: PayloadManager,
    results: list[RoundResult],
    request_id: str,
) -> tuple[int, str | None]:
    """Score every round. Returns (infra failures, last error class).

    `executor.map` yields in input order, so the report is deterministic.
    Exception TEXT can name extensions - this round's answer key - so only the
    class name is kept; the text goes to the log.
    """
    _setup_failures = 0
    _last_error: str | None = None
    _by_index = {_r.index: _r for _r in payload_manager.rounds}

    for _result in results:
        _kind: str | None = None
        _infra = False
        if _result.error is not None:
            _kind = type(_result.error).__name__
            _last_error = _kind
            if isinstance(_result.error, BrowserInfraError):
                # Our fault, not the miner's - excluded from the denominator.
                _infra = True
                _setup_failures += 1
                _reason = "lost the browser"
            else:
                _reason = "failed"
            logger.warning(
                f"[{request_id}] - Round {_result.index} {_reason}: {_result.error}"
            )
            # Same rule as above: the answer key only at DEBUG. Pairing the
            # failure with what was enabled is the whole point - a browser that
            # dies on one specific extension is otherwise invisible, because
            # `report()` reduces `error` to a boolean on purpose.
            _record = _by_index.get(_result.index)
            if _record is not None:
                logger.debug(
                    f"[{request_id}] - Round {_result.index} had "
                    f"{sorted(_record.enabled)} enabled when it {_reason}."
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

    _pool: list[str] = list(load_pool_names())
    _challenge_config = config.challenge

    _payload_manager = PayloadManager(pool=_pool)
    _payload_manager.build_schedule(
        n_rounds=_challenge_config.n_rounds,
        k=_challenge_config.k,
        coverage_bias=_challenge_config.coverage_bias,
    )

    # `BrowserSettings` mirrors `BrowserConfig` field for field; an unexpected
    # kwarg here means they have drifted, and that should fail loudly.
    _browser_settings = BrowserSettings(**_challenge_config.browser.declared_dump())

    logger.info(
        f"[{request_id}] - Running {len(_payload_manager.rounds)} round(s) "
        f"over a pool of {len(_pool)}..."
    )

    # Staged once - every round uses the same submission - and restored in the
    # `finally` so a miner's code never outlives its own run.
    _staged = ch_utils.stage_detection_files(miner_output)
    try:
        _results = _run_all_rounds(
            _payload_manager.rounds,
            pool=_pool,
            groups=load_pool_groups(),
            id_map=load_name_to_id(),
            page_url=bait_page_url(),
            settings=_browser_settings,
            request_id=request_id,
        )
        _setup_failures, _last_error = _record_all(
            _payload_manager, _results, request_id
        )

        # A thin average is indistinguishable from a solid one once it reaches
        # the validator as a bare float, so fail instead of returning it.
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
        global _last_report
        _last_report = _report
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
    "get_results",
    "score",
]
