import random

from pydantic import validate_call

from .schemas import MinerInput, MinerOutput


def get_task() -> MinerInput:
    return MinerInput()


@validate_call
def score(request_id: str, miner_output: MinerOutput) -> float:

    _score_result = random.random()  # nosec B311
    return _score_result


__all__ = [
    "get_task",
    "score",
]
