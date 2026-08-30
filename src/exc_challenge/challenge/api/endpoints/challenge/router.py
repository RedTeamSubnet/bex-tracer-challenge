import threading

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse

from api.core.constants import ErrorCodeEnum
from api.core.dependencies.auth import auth_api_key
from api.core.exceptions import BaseHTTPException
from api.logger import logger

from .schemas import MinerInput, MinerOutput
from . import service

router = APIRouter(tags=["Challenge"])

# Single-flight: one scoring run at a time. A run holds several Chrome
# instances and a fixed RAM budget, so overlapping runs would thrash the box
# and make every round's timing - and therefore its labels - unreliable.
# The endpoint is `def`, so FastAPI runs it in a threadpool and a threading
# lock is the right primitive.
_scoring_lock = threading.Lock()


@router.get(
    "/task",
    summary="Get task",
    description="This endpoint returns the task for the miner.",
    response_class=JSONResponse,
    response_model=MinerInput,
)
def get_task(request: Request):

    _request_id = request.state.request_id
    logger.info(f"[{_request_id}] - Getting task...")

    _miner_input: MinerInput
    try:
        _miner_input = service.get_task()

        logger.success(f"[{_request_id}] - Successfully got the task.")
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"[{_request_id}] - Failed to get task!")
        raise BaseHTTPException(
            error_enum=ErrorCodeEnum.INTERNAL_SERVER_ERROR,
            message="Failed to get task!",
        )

    return _miner_input


@router.post(
    "/score",
    summary="Score",
    description="This endpoint score miner output.",
    response_class=JSONResponse,
    responses={422: {}},
    dependencies=[Depends(auth_api_key)],
)
def post_score(request: Request, miner_input: MinerInput, miner_output: MinerOutput):

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

    _score: float = 0.0
    try:
        _score = service.score(request_id=_request_id, miner_output=miner_output)
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


__all__ = ["router"]
