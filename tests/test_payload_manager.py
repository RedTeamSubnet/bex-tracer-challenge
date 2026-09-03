"""Tests for the round schedule (ground truth) and MCC scoring."""

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "src/exc_challenge/challenge")
)

from api.endpoints.challenge._payload_manager import (  # noqa: E402
    PayloadManager,
    RoundStatus,
    build_round_schedule,
    mcc,
    score_round,
)

POOL = [f"ext{i:02d}" for i in range(30)]


def test_mcc_perfect_prediction():
    assert mcc(tp=5, tn=25, fp=0, fn=0) == pytest.approx(1.0)


def test_mcc_perfectly_wrong():
    assert mcc(tp=0, tn=0, fp=25, fn=5) == pytest.approx(-1.0)


def test_mcc_all_one_class_is_zero_not_nan():
    """The edge case that would otherwise divide by zero."""
    assert mcc(tp=5, tn=0, fp=25, fn=0) == 0.0  # answered all-true
    assert mcc(tp=0, tn=25, fp=0, fn=5) == 0.0  # answered all-false
    assert mcc(tp=0, tn=0, fp=0, fn=0) == 0.0  # empty


def test_mcc_known_confusion_matrix():
    # tp=3 tn=20 fp=2 fn=5
    #   num = 3*20 - 2*5 = 50
    #   den = sqrt(5 * 8 * 22 * 25) = sqrt(22000)
    assert mcc(tp=3, tn=20, fp=2, fn=5) == pytest.approx(50 / math.sqrt(22000))


def test_mcc_is_symmetric_under_class_swap():
    assert mcc(tp=7, tn=11, fp=3, fn=2) == pytest.approx(mcc(tp=11, tn=7, fp=2, fn=3))


def test_score_round_perfect():
    enabled = {"ext01", "ext02", "ext03"}
    pred = {e: (e in enabled) for e in POOL}
    assert score_round(POOL, enabled, pred) == pytest.approx(1.0)


def test_score_round_all_true_scores_zero():
    enabled = {"ext01", "ext02", "ext03"}
    pred = {e: True for e in POOL}
    assert score_round(POOL, enabled, pred) == 0.0


def test_score_round_all_false_scores_zero():
    enabled = {"ext01", "ext02", "ext03"}
    pred = {e: False for e in POOL}
    assert score_round(POOL, enabled, pred) == 0.0


def test_score_round_empty_dict_scores_zero():
    """Missing keys count as False, so an empty answer is the all-false case."""
    assert score_round(POOL, {"ext01", "ext02"}, {}) == 0.0


def test_score_round_missing_keys_treated_as_false():
    enabled = {"ext01", "ext02", "ext03"}
    full = {e: (e in enabled) for e in POOL}
    partial = {e: v for e, v in full.items() if v}  # only the True ones
    assert score_round(POOL, enabled, partial) == pytest.approx(
        score_round(POOL, enabled, full)
    )


def test_score_round_inverted_prediction_is_clamped_to_zero():
    enabled = {"ext01", "ext02", "ext03"}
    pred = {e: (e not in enabled) for e in POOL}
    assert score_round(POOL, enabled, pred) == 0.0  # raw MCC would be -1.0


def test_score_round_partial_beats_nothing():
    enabled = {"ext01", "ext02", "ext03", "ext04"}
    half = {e: (e in {"ext01", "ext02"}) for e in POOL}
    assert 0.0 < score_round(POOL, enabled, half) < 1.0


def test_random_guessing_averages_near_zero():
    """The property that makes MCC the right metric here."""
    rng = random.Random(1234)  # nosec B311
    scores = []
    for _ in range(400):
        k = rng.randint(3, 8)
        enabled = set(rng.sample(POOL, k))
        pred = {e: bool(rng.getrandbits(1)) for e in POOL}
        scores.append(score_round(POOL, enabled, pred))
    mean = sum(scores) / len(scores)
    # clamping at 0 gives a small positive bias; it must stay well under a
    # meaningful score. A fixed-k F1 metric would sit near 0.25 here.
    assert mean < 0.10, f"random guessing scored {mean:.3f}, floor is too high"


def test_schedule_length_and_k_bounds():
    sched = build_round_schedule(POOL, n_rounds=50, k_min=3, k_max=8)
    assert len(sched) == 50
    assert all(3 <= len(s) <= 8 for s in sched)


def test_schedule_subsets_are_drawn_from_pool():
    for subset in build_round_schedule(POOL, n_rounds=20, k_min=3, k_max=8):
        assert subset <= set(POOL)


def test_schedule_k_varies():
    """If k were constant, precision == recall and false positives stop costing."""
    sizes = {len(s) for s in build_round_schedule(POOL, 100, 3, 8)}
    assert len(sizes) > 1


def test_schedule_is_not_constant_across_runs():
    a = build_round_schedule(POOL, n_rounds=20, k_min=3, k_max=8)
    b = build_round_schedule(POOL, n_rounds=20, k_min=3, k_max=8)
    assert a != b


def test_schedule_fixed_k_allowed():
    assert all(len(s) == 5 for s in build_round_schedule(POOL, 10, 5, 5))


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"n_rounds": 0, "k_min": 3, "k_max": 8}, "n_rounds"),
        ({"n_rounds": 5, "k_min": 0, "k_max": 8}, "k_min"),
        ({"n_rounds": 5, "k_min": 9, "k_max": 3}, "k_min"),
        ({"n_rounds": 5, "k_min": 3, "k_max": 999}, "must be < pool size"),
    ],
)
def test_schedule_rejects_bad_params(kwargs, match):
    with pytest.raises(ValueError, match=match):
        build_round_schedule(POOL, **kwargs)


def test_schedule_rejects_empty_pool():
    with pytest.raises(ValueError, match="empty"):
        build_round_schedule([], n_rounds=5, k_min=1, k_max=1)


def test_manager_perfect_run_scores_one():
    mgr = PayloadManager(POOL)
    mgr.build_schedule(n_rounds=10, k_min=3, k_max=8)
    for rec in mgr.rounds:
        mgr.record(rec.index, {e: (e in rec.enabled) for e in POOL})
    assert mgr.calculate_score() == pytest.approx(1.0)


def test_manager_failed_round_scores_zero_and_does_not_raise():
    mgr = PayloadManager(POOL)
    mgr.build_schedule(n_rounds=4, k_min=3, k_max=5)
    for rec in mgr.rounds:
        mgr.record(rec.index, {e: (e in rec.enabled) for e in POOL})
    mgr.record(0, None, error="browser crashed")

    assert mgr.rounds[0].status is RoundStatus.FAILED
    assert mgr.rounds[0].score == 0.0
    assert mgr.calculate_score() == pytest.approx(0.75)


def test_manager_score_is_mean_over_all_rounds():
    mgr = PayloadManager(POOL)
    mgr.build_schedule(n_rounds=2, k_min=3, k_max=3)
    mgr.record(0, {e: (e in mgr.rounds[0].enabled) for e in POOL})  # 1.0
    mgr.record(1, {e: False for e in POOL})  # 0.0
    assert mgr.calculate_score() == pytest.approx(0.5)


def test_manager_score_always_within_unit_interval():
    rng = random.Random(7)  # nosec B311
    mgr = PayloadManager(POOL)
    mgr.build_schedule(n_rounds=30, k_min=3, k_max=8)
    for rec in mgr.rounds:
        mgr.record(rec.index, {e: bool(rng.getrandbits(1)) for e in POOL})
    assert 0.0 <= mgr.calculate_score() <= 1.0


def test_manager_no_rounds_scores_zero():
    assert PayloadManager(POOL).calculate_score() == 0.0


def test_manager_report_never_leaks_ground_truth():
    """The report must not reveal WHICH extensions were enabled."""
    mgr = PayloadManager(POOL)
    mgr.build_schedule(n_rounds=3, k_min=3, k_max=5)
    for rec in mgr.rounds:
        mgr.record(rec.index, {e: (e in rec.enabled) for e in POOL})
    mgr.calculate_score()

    blob = repr(mgr.report())
    for rec in mgr.rounds:
        for ext_id in rec.enabled:
            assert ext_id not in blob, f"report leaked enabled extension {ext_id}"


def test_manager_rejects_empty_pool():
    with pytest.raises(ValueError, match="empty"):
        PayloadManager([])


def test_public_dict_does_not_leak_the_enabled_set_via_the_error():
    """Browser errors name the extensions they tried to load, which is the
    round's answer key. The report must not carry that."""
    manager = PayloadManager(POOL)
    manager.build_schedule(n_rounds=1, k_min=3, k_max=3)
    enabled = sorted(manager.rounds[0].enabled)

    manager.record(0, None, error=f"not loaded, or ids drifted: {enabled}")
    published = manager.report()

    assert published["rounds"][0]["failed"] is True
    blob = repr(published)
    for ext_id in enabled:
        assert ext_id not in blob


def test_schedule_rejects_k_equal_to_the_pool_size():
    """Every extension enabled means no negative class: TN and FP are both 0,
    the MCC denominator vanishes, and a perfect prediction still scores 0.0."""
    with pytest.raises(ValueError, match="must be < pool size"):
        build_round_schedule(POOL, n_rounds=1, k_min=len(POOL), k_max=len(POOL))


def test_a_perfect_prediction_with_everything_enabled_would_have_scored_zero():
    """The reason the guard above exists, stated as a fact about the metric."""
    everything = set(POOL)
    perfect = {e: True for e in POOL}
    assert score_round(POOL, everything, perfect) == 0.0

