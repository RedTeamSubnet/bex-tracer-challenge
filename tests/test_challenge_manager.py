"""`EXCChallengeManager` against the real `redteam_core` base class.

Skipped unless a RedTeam checkout sits beside this repo. That is deliberate:
`redteam_core` is not a dependency of the challenge container - it runs in the
validator - so it must never become one just to let the suite pass. When the
checkout is there, these run against the genuine `ChallengeManager` and the
genuine `MinerChallengeCommit`, with only `bittensor` faked (the base class
uses it for `bt.logging` and a type hint; `models.py` does not import it).

What is being pinned here is one behaviour: a commit with nothing to compare
against keeps its score. Every sibling manager zeroes it, which zeroes every
miner in a new challenge's opening cycle. See .internal/design.md.
"""

import pathlib
import sys
import types

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
# `pytest.ini` ignores `src/`, and the other suites add
# `src/exc_challenge/challenge` for the `api.*` package. The manager lives one
# level up from that, as `exc_challenge.challenge_manager`, so it needs `src/`.
sys.path.insert(0, str(_REPO / "src"))
_REDTEAM_SRC = _REPO.parents[1] / "RedTeam" / "src"
pytestmark = pytest.mark.skipif(
    not (_REDTEAM_SRC / "redteam_core").is_dir(),
    reason="no RedTeam checkout beside this repo; redteam_core is a validator-side dep",
)


def _install_fake_bittensor() -> None:
    """`redteam_core` imports bittensor at module scope. Faking it is enough:
    nothing under test calls the chain."""
    if "bittensor" in sys.modules and hasattr(sys.modules["bittensor"], "Synapse"):
        return
    bt = types.ModuleType("bittensor")

    class _Log:
        def __getattr__(self, _name):
            return lambda *a, **kw: None

    class _Metagraph:  # only ever used as a type hint
        ...

    class _Synapse:  # redteam_core/protocol.py subclasses this at import
        def __init_subclass__(cls, **kw):
            pass

    bt.logging = _Log()
    bt.Metagraph = _Metagraph
    bt.metagraph = _Metagraph
    bt.Synapse = _Synapse
    sys.modules["bittensor"] = bt


@pytest.fixture(scope="module")
def manager_bits():
    _install_fake_bittensor()
    sys.path.insert(0, str(_REDTEAM_SRC))
    from redteam_core.validator.models import (  # noqa: E402
        ComparisonLog,
        MinerChallengeCommit,
        ScoringLog,
    )

    from exc_challenge.challenge_manager import EXCChallengeManager  # noqa: E402

    class FakeMetagraph:
        n = 8
        hotkeys = [f"hk{i}" for i in range(8)]

    # Mirrors the registration block in .internal/design.md. `challenge_incentive_weight`
    # and `comparison_config.max_unique_commits` are both bare subscripts in the
    # base class, so a missing one is a KeyError at construction - which is
    # exactly what this fixture would surface.
    info = {
        "name": "extension_classification_v1",
        "challenge_incentive_weight": 0.2,
        "comparison_config": {
            "min_acceptable_score": 0.6,
            "max_unique_commits": 15,
            "max_self_comparison_score": 0.8,
        },
    }
    return EXCChallengeManager(info, FakeMetagraph()), MinerChallengeCommit, ScoringLog, ComparisonLog


def _commit(MCC, SL, CL, uid, score, sims=None):
    commit = MCC(
        miner_uid=uid,
        miner_hotkey=f"hk{uid}",
        challenge_name="extension_classification_v1",
        docker_hub_id=f"img{uid}",
        encrypted_commit=f"enc{uid}",
        scoring_logs=[SL(score=score)],
    )
    if sims is not None:
        commit.comparison_logs = {"ref": [CL(similarity_score=s) for s in sims]}
    return commit


def test_a_commit_with_nothing_to_compare_against_keeps_its_score(manager_bits):
    """The opening-cycle case. `penalty` must stay None rather than being
    collapsed to 0.0, because 0.0 also means "compared, found nothing in
    common" and the siblings zero the score on it."""
    mgr, MCC, SL, CL = manager_bits
    commit = _commit(MCC, SL, CL, 0, 0.87, sims=None)
    mgr.update_miner_scores([commit])

    assert commit.penalty is None
    assert commit.score == pytest.approx(0.87)
    assert commit.accepted is True


def test_a_commit_compared_and_found_dissimilar_keeps_its_score(manager_bits):
    mgr, MCC, SL, CL = manager_bits
    commit = _commit(MCC, SL, CL, 1, 0.87, sims=[0.0])
    mgr.update_miner_scores([commit])

    assert commit.penalty == pytest.approx(0.0)
    assert commit.score == pytest.approx(0.87)


def test_similarity_below_the_threshold_is_not_penalised(manager_bits):
    mgr, MCC, SL, CL = manager_bits
    commit = _commit(MCC, SL, CL, 2, 0.87, sims=[0.30])
    mgr.update_miner_scores([commit])

    assert commit.score == pytest.approx(0.87)


def test_a_near_duplicate_still_collapses(manager_bits):
    """The fix must not weaken plagiarism detection - this is the behaviour
    the sibling managers have, and it has to survive unchanged."""
    mgr, MCC, SL, CL = manager_bits
    commit = _commit(MCC, SL, CL, 3, 0.87, sims=[0.95])
    mgr.update_miner_scores([commit])

    assert commit.score < 0.05
    assert commit.accepted is False


def test_a_score_below_min_score_is_not_accepted(manager_bits):
    """Random guessing scores ~0.09 on this pool; min_score is 0.3."""
    mgr, MCC, SL, CL = manager_bits
    commit = _commit(MCC, SL, CL, 4, 0.12, sims=None)
    mgr.update_miner_scores([commit])

    assert commit.accepted is False


def test_challenge_scores_are_a_normalised_distribution(manager_bits):
    mgr, MCC, SL, CL = manager_bits
    mgr.update_miner_scores(
        [
            _commit(MCC, SL, CL, 5, 0.87, sims=None),
            _commit(MCC, SL, CL, 6, 0.90, sims=[0.1]),
        ]
    )
    scores = mgr.get_challenge_scores()

    assert scores.shape == (8,)
    assert scores.sum() == pytest.approx(1.0)
    assert (scores >= 0).all()
