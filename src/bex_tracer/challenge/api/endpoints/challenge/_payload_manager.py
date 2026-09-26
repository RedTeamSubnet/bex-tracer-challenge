"""Ground truth, predictions and scoring for the extension-classification challenge.

`service.py` orchestrates and never computes a score. This module owns the round
schedule (which *is* the ground truth), the recorded predictions, and the metric:
MCC over every extension in the pool, per round, averaged.

See `docs/design.md` for why the metric is MCC rather than F1.
"""

import math
import secrets
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any

_EMPTY_POOL_ERROR = "extension pool is empty"


class RoundStatus(str, Enum):
    CREATED = "CREATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # The browser failed us, not the miner. Excluded from the score denominator.
    INFRA_FAILED = "INFRA_FAILED"


def mcc(tp: int, tn: int, fp: int, fn: int) -> float:
    """Matthews Correlation Coefficient.

    Returns 0.0 when the denominator vanishes, which is what happens when the
    miner answered all-one-class (all true or all false). That is the desired
    behaviour: such a submission carries no information.
    """
    numerator = (tp * tn) - (fp * fn)
    denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    if denominator == 0:
        return 0.0
    return numerator / denominator


def score_round(
    pool: list[str], enabled: set[str], predicted: dict[str, bool]
) -> float:
    """Score one round: MCC over every extension in the pool, clamped to [0, 1].

    A missing key in `predicted` counts as False - miners are not rewarded for
    omitting an answer they were unsure about.
    """
    tp = tn = fp = fn = 0
    for ext_id in pool:
        actual = ext_id in enabled
        guess = bool(predicted.get(ext_id, False))
        if guess and actual:
            tp += 1
        elif guess and not actual:
            fp += 1
        elif not guess and actual:
            fn += 1
        else:
            tn += 1
    return max(0.0, mcc(tp=tp, tn=tn, fp=fp, fn=fn))


def build_round_schedule(
    pool: list[str], n_rounds: int, k: int, coverage_bias: float = 1.0
) -> list[set[str]]:
    """Pick the enabled subset for each round. THIS IS THE GROUND TRUTH.

    Uses `secrets`, not `random` - the subset must not be predictable from any
    observable seed. It is never serialised anywhere the browser can reach.

    `k` is fixed, so the miner knows |enabled|: ranking the pool and taking the
    top k beats judging each extension, and predicting exactly k positives
    forces FP == FN. Deliberate. MCC still floors random guessing at ~0.

    `coverage_bias` trades even coverage against how inferable the schedule is.
    1.0 is uniform sampling; higher values favour extensions that have come up
    least. Measured at pool=25, k=5, 10 rounds:

        bias   never tested   score SD   worse miner outranks better
         1.0       2.66         0.049              32%
         2.0       0.38         0.032              23%
         3.0       0.04         0.027              21%

    Raising it is safe only while a miner cannot carry observations between
    rounds. Each round gets a fresh profile, so the only route is the network -
    which means this can go higher once egress from the bait page is closed.
    """
    if not pool:
        raise ValueError(_EMPTY_POOL_ERROR)
    if n_rounds < 1:
        raise ValueError(f"n_rounds must be >= 1, got {n_rounds}")
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if k >= len(pool):
        # With every extension enabled there is no negative class: TN and FP are
        # both 0, the MCC denominator vanishes, and even a perfect prediction
        # scores 0.0. The ceiling is one short of the pool.
        raise ValueError(
            f"k ({k}) must be < pool size ({len(pool)}); with every "
            f"extension enabled a perfect prediction still scores 0.0"
        )

    rng = secrets.SystemRandom()
    if coverage_bias <= 1.0:
        return [set(rng.sample(pool, k=k)) for _ in range(n_rounds)]

    # Weighted draw favouring the least-used extensions. Uniform sampling
    # leaves whole extensions untested - at 25/k=5/10 rounds, ~2.7 of them
    # never get enabled at all. An extension that is never enabled can only
    # COST a miner (a false positive is still punished) and can never earn
    # them anything, and a different set goes untested for every miner, so two
    # submissions are graded on different material.
    #
    # The weight is `coverage_bias ** -times_used`, which never reaches zero.
    # That matters: a rule that EXCLUDES a used extension makes late rounds
    # deducible by elimination, and with n_rounds*k a multiple of the pool
    # size the final round becomes fully determined - measured at 1.000 for an
    # attacker with no detection ability at all. Leaving every extension
    # reachable keeps the schedule balanced without ever making it inferable.
    _used: Counter[str] = Counter()
    _schedule: list[set[str]] = []
    for _ in range(n_rounds):
        _remaining, _picked = list(pool), set()
        while len(_picked) < k:
            _weights = [coverage_bias ** -_used[_e] for _e in _remaining]
            _cut, _acc = rng.random() * sum(_weights), 0.0
            for _e, _w in zip(_remaining, _weights):
                _acc += _w
                if _acc >= _cut:
                    break
            _picked.add(_e)
            _remaining.remove(_e)
        _used.update(_picked)
        _schedule.append(_picked)
    return _schedule


@dataclass
class RoundRecord:
    index: int
    enabled: set[str]
    status: RoundStatus = RoundStatus.CREATED
    score: float = 0.0
    error: str | None = None
    duration_sec: float | None = None

    def as_public_dict(self) -> dict[str, Any]:
        """Report shape. Contains no ground truth, so it is safe to serialise.

        `error` is deliberately reduced to a flag. Browser failures name the
        extensions they were trying to load - `not loaded, or ids drifted:
        [...]` - which is exactly this round's answer key. The full text stays
        on `self.error` for the server log.
        """
        return {
            "index": self.index,
            "status": self.status.value,
            "n_enabled": len(self.enabled),
            "score": round(self.score, 4),
            "failed": self.error is not None,
            "duration_sec": self.duration_sec,
        }


class PayloadManager:
    """Per-run ground truth and predictions.

    One instance per run rather than a module-level singleton, so that two
    overlapping /score calls cannot corrupt each other's state.
    """

    def __init__(self, pool: list[str]) -> None:
        if not pool:
            raise ValueError(_EMPTY_POOL_ERROR)
        self.pool: list[str] = list(pool)
        self.rounds: list[RoundRecord] = []

    def build_schedule(
        self, n_rounds: int, k: int, coverage_bias: float = 1.0
    ) -> None:
        schedule = build_round_schedule(self.pool, n_rounds, k, coverage_bias)
        self.rounds = [
            RoundRecord(index=i, enabled=enabled) for i, enabled in enumerate(schedule)
        ]

    def record(
        self,
        index: int,
        predicted: dict[str, bool] | None,
        *,
        error: str | None = None,
        duration_sec: float | None = None,
        infra: bool = False,
    ) -> float:
        """Record one round's result and return its score.

        A failed or empty round scores 0 rather than raising - one bad round must
        not abort the run.

        `infra=True` marks a round the BROWSER lost - dropped from the score
        denominator by `scored_rounds()`. Miner failures are not.
        """
        rec = self.rounds[index]
        rec.duration_sec = duration_sec
        rec.error = error

        if predicted is None:
            rec.status = RoundStatus.INFRA_FAILED if infra else RoundStatus.FAILED
            rec.score = 0.0
            return 0.0

        rec.score = score_round(self.pool, rec.enabled, predicted)
        rec.status = RoundStatus.COMPLETED
        return rec.score

    def scored_rounds(self) -> list[RoundRecord]:
        """Rounds that count toward the score. Infra failures are excluded.

        Our browser dying is not evidence about the miner - averaging a 0 in
        charges them for our bug. A miner's own failure IS evidence, and must
        stay in: otherwise throwing on every uncertain round would raise its
        mean. Dropping ours only shrinks the sample, which the caller's
        setup-failure guard already checks.
        """
        return [r for r in self.rounds if r.status != RoundStatus.INFRA_FAILED]

    def calculate_score(self) -> float:
        """Mean over the rounds that count. Returns 0.0 if none did."""
        scored = self.scored_rounds()
        if not scored:
            return 0.0
        return sum(rec.score for rec in scored) / len(scored)

    def report(self) -> dict[str, Any]:
        """Recomputes the score, so the report cannot go stale if a caller
        forgets to call `calculate_score()` first."""
        completed = [rec for rec in self.rounds if rec.status == RoundStatus.COMPLETED]
        return {
            "pool_size": len(self.pool),
            "n_rounds": len(self.rounds),
            "n_completed": len(completed),
            "n_scored": len(self.scored_rounds()),
            "score": round(self.calculate_score(), 4),
            "rounds": [rec.as_public_dict() for rec in self.rounds],
        }


__all__ = [
    "RoundStatus",
    "mcc",
    "score_round",
    "build_round_schedule",
    "RoundRecord",
    "PayloadManager",
]
