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
sys.path.insert(0, str(REPO / "src/exc_challenge/challenge"))

from api.config import config  # noqa: E402
from api.main import app  # noqa: E402
from api.endpoints.challenge import service  # noqa: E402
from api.endpoints.challenge._pool import load_pool_groups, load_pool_ids  # noqa: E402

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
    load_pool_groups.cache_clear()

    pool_size = len(load_pool_ids())
    monkeypatch.setattr(config.challenge, "n_rounds", 12)
    monkeypatch.setattr(config.challenge, "k_min", 1)
    monkeypatch.setattr(config.challenge, "k_max", max(1, pool_size - 1))

    yield
    load_pool_ids.cache_clear()
    load_pool_groups.cache_clear()


def fake_browser(answer):
    """Stand in for `run_round`, answering as `answer(enabled, pool)` says."""

    def _run_round(subset, *, pool, **_kwargs):
        return answer(set(subset), list(pool))

    return _run_round


def score_with(monkeypatch, answer) -> float:
    monkeypatch.setattr(service, "run_round", fake_browser(answer))
    return service.score(request_id="test", miner_output=solution("// stub"))


def solution(content: str):
    """One file per published group, all sharing `content`. The browser layer
    is stubbed by `fake_browser` in every caller, so the JS text itself is
    never executed - only the file names have to satisfy `MinerOutput`."""
    from api.endpoints.challenge.schemas import CommitFilePM, MinerOutput

    return MinerOutput(
        commit_files=[
            CommitFilePM(file_name=f"{name}.js", content=content)
            for name in load_pool_groups()
        ]
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


def payload() -> dict:
    """A complete, valid submission: one stub file per published group."""
    return {
        "miner_input": {"random_val": "abc123"},
        "miner_output": {
            "commit_files": [
                {
                    "file_name": f"{name}.js",
                    "content": f"window.detect_{name} = async () => ({{}});",
                }
                for name in load_pool_groups()
            ]
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


def test_score_rejects_a_key_with_illegal_characters(client):
    """Reaches the charset check - `nope` is too short and stops at the length
    guard, so without this the pattern branch is never exercised."""
    response = client.post(
        "/score", json=payload(), headers={"X-API-Key": "has spaces and.dots"}
    )
    assert response.status_code == 401


def test_score_rejects_a_well_formed_but_wrong_key(client):
    """Well-formed and long enough, so this is the only test that reaches
    compare_digest - the branch that actually decides."""
    response = client.post(
        "/score", json=payload(), headers={"X-API-Key": "a" * 40}
    )
    assert response.status_code == 401


def test_score_rejects_a_wrongly_named_file(client):
    body = payload()
    body["miner_output"]["commit_files"][0]["file_name"] = "detect.js"
    response = client.post("/score", json=body, headers={"X-API-Key": API_KEY})
    assert response.status_code == 422


def test_score_rejects_more_than_one_file_for_the_same_group(client):
    body = payload()
    body["miner_output"]["commit_files"].append(
        dict(body["miner_output"]["commit_files"][0])
    )
    response = client.post("/score", json=body, headers={"X-API-Key": API_KEY})
    assert response.status_code == 422


def test_score_rejects_a_missing_group_file(client):
    body = payload()
    body["miner_output"]["commit_files"].pop()
    response = client.post("/score", json=body, headers={"X-API-Key": API_KEY})
    assert response.status_code == 422


def test_score_rejects_a_too_long_submission(client):
    long_js = "\n".join(
        f"// line {i}" for i in range(config.challenge.submission_max_lines + 1)
    )
    body = payload()
    body["miner_output"]["commit_files"][0]["content"] = long_js
    response = client.post("/score", json=body, headers={"X-API-Key": API_KEY})
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


def test_record_all_stores_the_error_class_not_its_text():
    """A browser failure names the extensions it tried to load - that set is the
    round's answer key. `_record_all` must keep only the exception class name on
    the record; the full text goes to the log and nowhere else.

    This exercises `service._record_all` directly, because that is where the
    sanitisation lives. Asserting on a hand-written `record(error=...)` call
    would prove nothing.
    """
    from api.endpoints.challenge._browser import BrowserInfraError
    from api.endpoints.challenge._payload_manager import PayloadManager

    pool = list(load_pool_ids())
    manager = PayloadManager(pool=pool)
    manager.build_schedule(n_rounds=1, k_min=2, k_max=2)
    enabled = sorted(manager.rounds[0].enabled)

    failure = BrowserInfraError(f"2 of 2 extension(s) did not load: {enabled}")
    results = [service.RoundResult(0, None, failure, 1.0)]

    service._record_all(manager, results, "test")

    assert manager.rounds[0].error == "BrowserInfraError"
    blob = repr(manager.report())
    for ext_id in enabled:
        assert ext_id not in blob
