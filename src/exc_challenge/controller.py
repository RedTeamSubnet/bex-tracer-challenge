import os

import bittensor as bt
import requests

from redteam_core.challenge_pool.controller import Controller
from redteam_core.validator.models import MinerChallengeCommit

# The challenge container publishes on this port; `controller.py` in
# redteam_core is what maps it, so it is fixed rather than configurable here.
_CHALLENGE_BASE_URL = "http://localhost:10001"


class EXCController(Controller):

    def __init__(
        self,
        challenge_name: str,
        challenge_info: dict,
        miner_commits: list[MinerChallengeCommit],
        reference_comparison_commits: list[MinerChallengeCommit],
        seed_inputs: list[dict] = [],
    ):

        super().__init__(
            challenge_name,
            challenge_info,
            miner_commits,
            reference_comparison_commits,
            seed_inputs,
        )
        comparison_config = self.challenge_info.get("comparison_config", {})
        self.comparison_min_acceptable_score = comparison_config.get(
            "min_acceptable_score", 0.6
        )

    def _score_miner_with_new_inputs(
        self, miner_commit: MinerChallengeCommit, challenge_inputs
    ) -> None:
        _scoring_log = miner_commit.scoring_logs[0]
        for i, miner_input in enumerate(challenge_inputs):

            # `get_higest_comparison_score()` returns 0.0 both when a
            # comparison ran and found nothing in common AND when no comparison
            # ran at all. The ADA controller treats the second case as a
            # failure and skips scoring (`or _higest_comparison_score == 0.0`),
            # which is fatal for a challenge that has just launched: there are
            # no reference commits yet, so every miner scores 0 and the browser
            # never even runs. `comparison_logs` is what actually distinguishes
            # the two, so gate on that instead.
            _has_comparison = bool(miner_commit.comparison_logs)
            _higest_comparison_score = miner_commit.get_higest_comparison_score()
            if (
                _has_comparison
                and _higest_comparison_score >= self.comparison_min_acceptable_score
            ):
                bt.logging.info(
                    f"[CONTROLLER - EXCController] Skipping scoring for miner {miner_commit.miner_hotkey} on task {i} "
                    f"due to high comparison score: {_higest_comparison_score}"
                )
                _scoring_log.score = 0.0
                if _scoring_log.error:
                    _scoring_log.error += (
                        " | Skipped scoring due to high comparison score."
                    )
                else:
                    _scoring_log.error = "Skipped scoring due to high comparison score."
                continue

            score = (
                self._score_challenge(
                    miner_input=miner_input,
                    miner_output=_scoring_log.miner_output,
                    task_id=i,
                )
                if _scoring_log.miner_output is not None
                else 0.0
            )

            _scoring_log.score = score

        # The run report is per-round metadata (durations, which rounds were
        # excluded as infra failures) with no ground truth in it - see
        # `RunReportPM`. Attaching it here is the only way those numbers reach
        # the validator's records; without it an infra failure in production is
        # invisible after the fact.
        _scoring_log.miner_output["scoring_results"] = self._get_results_from_challenge()

        return

    def _get_results_from_challenge(self) -> dict:
        """`GET /results` from the challenge container.

        Best-effort: this is diagnostic metadata, so a failure here must never
        cost a miner their score. Returns {} and logs instead of raising.
        """
        try:
            response = requests.get(
                f"{_CHALLENGE_BASE_URL}/results",
                timeout=5,
                verify=False,  # nosec B501 - loopback to our own container
                headers={"X-API-KEY": os.environ.get("RT_CHALLENGE_API_KEY", "")},
            )
            response.raise_for_status()
            return response.json() if response.content else {}
        except Exception as exc:  # noqa: BLE001 - diagnostics must not throw
            bt.logging.error(
                f"[CONTROLLER - EXCController] Unable to fetch /results: {exc}"
            )
            return {}

    def _exclude_output_keys(self, miner_output: dict, reference_output: dict) -> None:
        """Drop `scoring_results` before outputs are compared.

        `commit_files` is deliberately NOT dropped, which is where this differs
        from the ADA controller. It is the only thing a miner actually submits
        here, so excluding it would leave nothing to compare and similarity
        would be meaningless for every pair.

        `scoring_results` must go, though: round durations and which rounds hit
        an infra failure vary run to run for reasons that have nothing to do
        with the miner, so leaving them in makes identical submissions look
        different and unrelated ones look alike.
        """
        miner_output["scoring_results"] = None
        reference_output["scoring_results"] = None


__all__ = [
    "EXCController",
]
