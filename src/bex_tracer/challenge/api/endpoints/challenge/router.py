import threading

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from api.core.constants import ErrorCodeEnum
from api.core.dependencies.auth import auth_api_key
from api.core.exceptions import BaseHTTPException
from api.logger import logger
from api.mount import BAIT_INDEX, LOOPBACK_HOSTS

from .schemas import  MinerOutput, RunReportPM
from . import service

router = APIRouter(tags=["Challenge"])

_scoring_lock = threading.Lock()

@router.post(
    "/score",
    summary="Score",
    description="This endpoint score miner output.",
    response_class=JSONResponse,
    responses={422: {}},
    dependencies=[Depends(auth_api_key)],
)
def post_score(request: Request, miner_output: MinerOutput):
    """Score a submission.

    `miner_output` is part of the subnet-wide /score contract - the validator
    posts back the output it received - but it is deliberately NOT forwarded to
    `service.score()`. The pool must come from our own extensions.yml, never
    from the request: a caller-supplied pool of one extension would make MCC
    trivial to max. Do not "fix" this by passing it through.
    """

    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Scoring the miner output...")

    if not _scoring_lock.acquire(blocking=False):
        logger.warning(
            f"[{_request_id}] - Rejected: a scoring run is already in progress."
        )
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
            message="A scoring run is already in progress!",
        )

    try:
        _score: float = service.score(request_id=_request_id, miner_output=miner_output)
        logger.success(
            f"[{_request_id}] - Successfully scored the miner output: {_score}"
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"[{_request_id}] - Failed to score the miner output!")
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.INTERNAL_SERVER_ERROR,
            message="Failed to score the miner output!",
        )
    finally:
        _scoring_lock.release()

    return _score


@router.get(
    "/results",
    summary="Report on the most recent scoring run",
    description=(
        "Per-round outcome of the last /score call: how many rounds completed, "
        "which failed, timings, detected extensions, and the final score."
    ),
    response_class=JSONResponse,
    response_model=RunReportPM,
    responses={404: {}},
    dependencies=[Depends(auth_api_key)],
)
def get_results(request: Request):
    """Behind the same key as /score.

    The report exposes the last-scored miner's detected extensions, which is not
    something a rival should be able to read off an open port.
    """
    _request_id = request.state.request_id
    _report = service.get_results()

    if _report is None:
        logger.info(f"[{_request_id}] - No scoring run has completed yet.")
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.NOT_FOUND,
            message="No scoring run has completed yet!",
        )

    return _report


@router.get(
    "/_web",
    summary="Serves the bait page",
    name="web_ui",
    include_in_schema=False,
)
def _get_web(request: Request) -> FileResponse:
    """Serve the bait page.

    Sent from disk unchanged - deliberately NOT rendered from a template the way
    the sibling challenges do it. Nothing per-request may reach this HTML: the
    page is the one artefact the miner's script can read, so a value injected
    here is a channel for leaking which extensions the round enabled.

    Assets stay relative (`static/...`), which resolves against `/` from this
    path and lands on the `/static` mount.
    """
    _client = request.client
    if (_client.host if _client else None) not in LOOPBACK_HOSTS:
        # 404, not 403 - do not confirm the path exists. Matches the /static
        # mount's wrapper; see api/mount.py.
        raise HTTPException(status_code=404, detail="Not Found")

    return FileResponse(BAIT_INDEX, media_type="text/html")


__all__ = ["router"]
