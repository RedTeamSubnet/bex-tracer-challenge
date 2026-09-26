"""Step 5 acceptance: the /score path, with the browser stubbed out.

`run_round()` is the only piece that needs a real Chrome, so it is replaced by a
fake that answers the way a given miner would. Everything else - schedule,
recording, metric, auth, single-flight - is the real code.
"""

import random
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src/bex_tracker/challenge"))

from api.config import config  # noqa: E402
from api.main import app  # noqa: E402
from api.endpoints.challenge import service  # noqa: E402
from api.endpoints.challenge._pool import load_pool_ids  # noqa: E402

POOL_PATH = REPO / "extensions.yml"
API_KEY = config.challenge.api_key.get_secret_value()


@pytest.fixture(autouse=True)
def challenge_config(monkeypatch):
    """Point the app at the repo's real pool, with a k-range it can satisfy.

    The shipped defaults are k=[3,8] against a pool of 4, which cannot be
    sampled - see the Step 1 blockers in docs/BUILD.md.
    """
    monkeypatch.setattr(config.challenge, "pool_path", str(POOL_PATH))
    load_pool_ids.cache_clear()

    pool_size = len(load_pool_ids())
    monkeypatch.setattr(config.challenge, "n_rounds", 12)
    monkeypatch.setattr(config.challenge, "k_min", 1)
    monkeypatch.setattr(config.challenge, "k_max", max(1, pool_size - 1))

    yield
    load_pool_ids.cache_clear()


def fake_browser(answer):
    """Stand in for `run_round`, answering as `answer(enabled, pool)` says."""

    def _run_round(subset, miner_js, *, pool, **_kwargs):
        return answer(set(subset), list(pool))

    return _run_round


def score_with(monkeypatch, answer) -> float:
    monkeypatch.setattr(service, "run_round", fake_browser(answer))
    return service.score(request_id="test", miner_output=solution("// stub"))


def solution(content: str):
    from api.endpoints.challenge.schemas import CommitFilePM, MinerOutput

    return MinerOutput(
        commit_files=[CommitFilePM(file_name="solution.js", content=content)]
    )


# -- the metric rejects uninformative miners -------------------------------


def test_all_true_scores_zero(monkeypatch):
    score = score_with(monkeypatch, lambda enabled, pool: {e: True for e in pool})
    assert score == 0.0


def test_all_false_scores_zero(monkeypatch):
    score = score_with(monkeypatch, lambda enabled, pool: {e: False for e in pool})
    assert score == 0.0


def test_random_guessing_scores_near_zero(monkeypatch):
    """A coin-flip miner earns nothing it can rely on.

    The schedule comes from `secrets.SystemRandom()` and cannot be seeded, so
    this averages over enough rounds for the mean to settle. At the fixture's
    12 rounds the spread is wide enough to cross any useful bound by luck -
    which is the same reason `n_rounds` defaults to 20 in production.
    """
    monkeypatch.setattr(config.challenge, "n_rounds", 200)  # the config cap
    rng = random.Random(1234)  # nosec B311
    score = score_with(
        monkeypatch, lambda enabled, pool: {e: rng.random() < 0.5 for e in pool}
    )
    assert 0.0 <= score < 0.35


def test_a_perfect_miner_scores_one(monkeypatch):
    score = score_with(
        monkeypatch, lambda enabled, pool: {e: e in enabled for e in pool}
    )
    assert score == pytest.approx(1.0)


def test_a_failing_round_scores_zero_and_the_run_continues(monkeypatch):
    calls = {"n": 0}

    def flaky(enabled, pool):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("chrome fell over")
        return {e: e in enabled for e in pool}

    score = score_with(monkeypatch, flaky)
    assert calls["n"] == config.challenge.n_rounds
    # One zeroed round out of N, the rest perfect.
    assert score == pytest.approx(
        (config.challenge.n_rounds - 1) / config.challenge.n_rounds
    )


def test_every_round_failing_to_start_raises_rather_than_scoring_zero(monkeypatch):
    """A 0.0 here would read as "the miner earned nothing" when the truth is
    "no browser ever started". The validator cannot tell those apart, so the
    request must fail instead."""
    from api.endpoints.challenge._browser import BrowserInfraError

    def cannot_start(enabled, pool):
        raise BrowserInfraError("/run/exc is mounted noexec")

    with pytest.raises(RuntimeError, match="failed to start a browser"):
        score_with(monkeypatch, cannot_start)


def test_a_miner_whose_script_always_throws_still_scores_zero(monkeypatch):
    """The miner CAN make its own script fail, so that must stay a 0.0 and must
    not be mistaken for broken infrastructure."""
    from api.endpoints.challenge._browser import BrowserError

    def miner_throws(enabled, pool):
        raise BrowserError("miner script threw: ReferenceError")

    assert score_with(monkeypatch, miner_throws) == 0.0


def test_a_partial_setup_failure_still_scores(monkeypatch):
    """Only an all-rounds setup failure is fatal; one flaky launch is not."""
    from api.endpoints.challenge._browser import BrowserInfraError

    calls = {"n": 0}

    def flaky_launch(enabled, pool):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrowserInfraError("transient launch failure")
        return {e: e in enabled for e in pool}

    score = score_with(monkeypatch, flaky_launch)
    expected = (config.challenge.n_rounds - 1) / config.challenge.n_rounds
    assert score == pytest.approx(expected)


# -- the published task ------------------------------------------------------


def test_get_task_publishes_the_pool():
    task = service.get_task()
    assert task.extension_ids == list(load_pool_ids())
    assert len(task.extension_ids) >= 1


def test_rejected_extensions_are_not_published():
    # uBlock Origin proper is manifest v2 and sits under `rejected:`.
    assert "cjpalhdlnbpafiamejdnhcphjbkeiagm" not in load_pool_ids()


# -- the endpoint ------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_round",
        fake_browser(lambda enabled, pool: {e: e in enabled for e in pool}),
    )
    return TestClient(app)


def payload(file_name: str = "solution.js", content: str = "// stub") -> dict:
    return {
        "miner_input": {"random_val": "abc123"},
        "miner_output": {
            "commit_files": [{"file_name": file_name, "content": content}]
        },
    }


def test_score_returns_a_bare_float_in_range(client):
    response = client.post("/score", json=payload(), headers={"X-API-Key": API_KEY})
    assert response.status_code == 200

    score = response.json()
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0


def test_score_requires_the_api_key(client):
    assert client.post("/score", json=payload()).status_code == 401


def test_score_rejects_a_wrong_api_key(client):
    response = client.post("/score", json=payload(), headers={"X-API-Key": "nope"})
    assert response.status_code == 401


def test_score_rejects_a_wrongly_named_file(client):
    response = client.post(
        "/score", json=payload(file_name="detect.js"), headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 422


def test_score_rejects_more_than_one_file(client):
    body = payload()
    body["miner_output"]["commit_files"].append(
        {"file_name": "solution.js", "content": "// second"}
    )
    response = client.post("/score", json=body, headers={"X-API-Key": API_KEY})
    assert response.status_code == 422


def test_score_rejects_a_too_long_submission(client):
    long_js = "\n".join(
        f"// line {i}" for i in range(config.challenge.submission_max_lines + 1)
    )
    response = client.post(
        "/score", json=payload(content=long_js), headers={"X-API-Key": API_KEY}
    )
    assert response.status_code == 422


def test_task_publishes_the_pool_over_http(client):
    response = client.get("/task")
    assert response.status_code == 200
    assert response.json()["extension_ids"] == list(load_pool_ids())


def test_the_bait_page_is_served(client):
    response = client.get("/_web/index.html")
    assert response.status_code == 200
    assert "<html" in response.text.lower()


def test_only_one_scoring_run_at_a_time(client, monkeypatch):
    """The second caller is rejected rather than queued behind the first."""
    import threading

    from api.endpoints.challenge import router as challenge_router

    entered = threading.Event()
    release = threading.Event()

    def blocking_score(request_id: str, miner_output):
        entered.set()
        release.wait(timeout=10)
        return 0.5

    monkeypatch.setattr(challenge_router.service, "score", blocking_score)

    result: dict[str, int] = {}
    first = threading.Thread(
        target=lambda: result.update(
            first=client.post(
                "/score", json=payload(), headers={"X-API-Key": API_KEY}
            ).status_code
        )
    )
    first.start()
    assert entered.wait(timeout=10), "first request never reached the service"

    second = client.post("/score", json=payload(), headers={"X-API-Key": API_KEY})
    assert second.status_code == 429

    release.set()
    first.join(timeout=10)
    assert result["first"] == 200


def test_rounds_run_in_parallel_up_to_the_configured_limit(monkeypatch):
    """`max_parallel_rounds` was declared in config but read by nothing, so
    rounds ran one at a time. Pin that it is actually honoured."""
    import threading

    monkeypatch.setattr(config.challenge, "n_rounds", 8)
    monkeypatch.setattr(config.challenge, "max_parallel_rounds", 4)

    lock = threading.Lock()
    state = {"live": 0, "peak": 0}

    def slow_round(enabled, pool):
        with lock:
            state["live"] += 1
            state["peak"] = max(state["peak"], state["live"])
        time.sleep(0.05)
        with lock:
            state["live"] -= 1
        return {e: e in enabled for e in pool}

    score = score_with(monkeypatch, slow_round)

    assert score == pytest.approx(1.0)
    assert state["peak"] > 1, "rounds still ran sequentially"
    assert state["peak"] <= 4, f"exceeded the configured limit: {state['peak']}"


def test_results_are_recorded_in_index_order_despite_finish_order(monkeypatch):
    """Workers finish out of order; the report must not."""
    monkeypatch.setattr(config.challenge, "n_rounds", 6)
    monkeypatch.setattr(config.challenge, "max_parallel_rounds", 6)

    def jittered(enabled, pool):
        time.sleep(0.02 * (len(enabled) % 3))
        return {e: e in enabled for e in pool}

    score_with(monkeypatch, jittered)


def test_a_mostly_broken_run_raises_instead_of_returning_a_deflated_score(monkeypatch):
    """Measured 2026-08-30: with max_parallel_rounds=4 only 1 of 4 rounds
    started, and /score returned 0.1654 as though the miner had earned it."""
    from api.endpoints.challenge._browser import BrowserInfraError

    monkeypatch.setattr(config.challenge, "n_rounds", 4)
    calls = {"n": 0}

    def mostly_broken(enabled, pool):
        calls["n"] += 1
        if calls["n"] > 1:
            raise BrowserInfraError("not loaded, or ids drifted: [...] (loaded: [])")
        return {e: e in enabled for e in pool}

    with pytest.raises(RuntimeError, match="3 of 4 round"):
        score_with(monkeypatch, mostly_broken)


def test_one_flaky_launch_is_tolerated(monkeypatch):
    """A single bad launch is noise, not a broken run - it must still score."""
    from api.endpoints.challenge._browser import BrowserInfraError

    monkeypatch.setattr(config.challenge, "n_rounds", 20)
    calls = {"n": 0}

    def one_bad(enabled, pool):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrowserInfraError("transient")
        return {e: e in enabled for e in pool}

    assert score_with(monkeypatch, one_bad) == pytest.approx(19 / 20)
