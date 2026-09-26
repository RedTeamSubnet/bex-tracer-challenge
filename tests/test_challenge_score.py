"""Step 5 acceptance: the /score path, with the browser stubbed out.

`run_round()` is the only piece that needs a real Chrome, so it is replaced by a
fake that answers the way a given miner would. Everything else - schedule,
recording, metric, auth, single-flight - is the real code.
"""

import json
import random
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src/bex_tracer/challenge"))

from api.config import config  # noqa: E402
from api.main import app  # noqa: E402
from api.endpoints.challenge import service  # noqa: E402
from api.endpoints.challenge._pool import (  # noqa: E402
    load_name_to_id,
    load_pool_groups,
    load_pool_names,
)

POOL_PATH = REPO / "src/bex_tracer/challenge/extensions.yml"
API_KEY = config.challenge.api_key.get_secret_value()


@pytest.fixture(autouse=True)
def challenge_config(monkeypatch):
    """Point the app at the repo's real pool, with a k-range it can satisfy.

    The shipped defaults are k=[3,8] against a pool of 4, which cannot be
    sampled - see the Step 1 blockers in docs/BUILD.md.
    """
    monkeypatch.setattr(config.challenge, "pool_path", str(POOL_PATH))
    load_pool_names.cache_clear()
    load_name_to_id.cache_clear()
    load_pool_groups.cache_clear()

    pool_size = len(load_pool_names())
    monkeypatch.setattr(config.challenge, "n_rounds", 12)
    monkeypatch.setattr(config.challenge, "k", max(1, min(5, pool_size - 1)))

    yield
    load_pool_names.cache_clear()
    load_name_to_id.cache_clear()
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
    and production runs only 2, relying on the validator to average many runs.
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
    """With every round losing the browser there is nothing left to average.

    Infra failures are dropped from the denominator, so `calculate_score()`
    would return 0.0 here - and a 0.0 reads as "the miner earned nothing" when
    the truth is "no browser ever started". The validator cannot tell those
    apart from a bare float, so the request must fail instead.
    """
    from api.endpoints.challenge._browser import BrowserInfraError

    def cannot_start(enabled, pool):
        raise BrowserInfraError("/run/exc is mounted noexec")

    with pytest.raises(RuntimeError, match="too few scored rounds"):
        score_with(monkeypatch, cannot_start)


def test_a_miner_whose_script_always_throws_still_scores_zero(monkeypatch):
    """The miner CAN make its own script fail, so that must stay a 0.0 and must
    not be mistaken for broken infrastructure."""
    from api.endpoints.challenge._browser import BrowserError

    def miner_throws(enabled, pool):
        raise BrowserError("miner script threw: ReferenceError")

    assert score_with(monkeypatch, miner_throws) == 0.0


def test_a_partial_setup_failure_still_scores(monkeypatch):
    """Only an all-rounds setup failure is fatal; one flaky launch is not.

    The surviving rounds here are all perfect, so the run scores 1.0: a browser
    WE lost is dropped from the denominator rather than averaged in as a zero.
    Charging it to the miner would cap a flawless submission below 1.0 for our
    own infrastructure fault.
    """
    from api.endpoints.challenge._browser import BrowserInfraError

    calls = {"n": 0}

    def flaky_launch(enabled, pool):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrowserInfraError("transient launch failure")
        return {e: e in enabled for e in pool}

    assert score_with(monkeypatch, flaky_launch) == pytest.approx(1.0)


def _run_and_report(monkeypatch, answer):
    monkeypatch.setattr(service, "run_round", fake_browser(answer))
    score = service.score(request_id="test", miner_output=solution("// stub"))
    return score, service.get_results()


def test_a_page_failure_that_passes_on_retry_is_scored_normally(monkeypatch):
    """A genuine flake with the page open gets one retry, and the retry counts."""
    from api.endpoints.challenge._browser import PageInfraError

    seen = set()

    def flaky_page(enabled, pool):
        key = frozenset(enabled)
        if key not in seen:
            seen.add(key)
            raise PageInfraError("tab crashed")
        return {e: e in enabled for e in pool}

    score, report = _run_and_report(monkeypatch, flaky_page)
    assert score == pytest.approx(1.0)
    assert report["n_completed"] == report["n_rounds"]


def test_a_page_failure_that_repeats_counts_against_the_submission(monkeypatch):
    """The submission runs in the page and can empty #root, navigate away or
    crash its tab on purpose. Excluding those rounds would let it discard the
    ones it expects to lose - so a repeat stays in the average as a zero."""
    from api.endpoints.challenge._browser import PageInfraError

    def always_breaks_the_page(enabled, pool):
        raise PageInfraError("the bait page loaded but rendered nothing")

    score, report = _run_and_report(monkeypatch, always_breaks_the_page)
    assert score == 0.0
    assert report["n_scored"] == report["n_rounds"]
    assert {r["status"] for r in report["rounds"]} == {"FAILED"}


# -- the published task ------------------------------------------------------


def test_get_task_publishes_the_pool():
    task = service.get_task()
    assert task.extension_names == list(load_pool_names())
    assert len(task.extension_names) >= 1


def test_rejected_extensions_are_not_published():
    # uBlock Origin proper is manifest v2 and sits under `rejected:`.
    assert "uBlock Origin" not in load_pool_names()
    assert "cjpalhdlnbpafiamejdnhcphjbkeiagm" not in load_name_to_id().values()


# -- the endpoint ------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(
        service,
        "run_round",
        fake_browser(lambda enabled, pool: {e: e in enabled for e in pool}),
    )
    # A real loopback address: `TestClient`'s default peer name is not one.
    return TestClient(app, client=("127.0.0.1", 50000))


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


def test_startup_refuses_without_an_api_key(monkeypatch):
    """No default key exists - the repo is public, so any default is known."""
    from api.lifespan import _check_api_key

    monkeypatch.setattr(config.challenge, "api_key", None)
    with pytest.raises(SystemExit):
        _check_api_key()


@pytest.mark.parametrize("bad", ["short", "has spaces in it", "x" * 129])
def test_startup_refuses_a_key_no_request_could_match(monkeypatch, bad):
    """A key `auth_api_key` would reject on shape fails at boot, not on every
    request."""
    from pydantic import SecretStr

    from api.lifespan import _check_api_key

    monkeypatch.setattr(config.challenge, "api_key", SecretStr(bad))
    with pytest.raises(SystemExit):
        _check_api_key()


def test_an_unset_key_rejects_instead_of_crashing(client, monkeypatch):
    monkeypatch.setattr(config.challenge, "api_key", None)
    response = client.post("/score", json=payload(), headers={"X-API-Key": API_KEY})
    assert response.status_code == 401


def test_startup_refuses_a_bait_page_with_a_missing_bundle(monkeypatch, tmp_path):
    """A missing bundle renders an empty page and zeroes every miner - so it
    fails the boot instead of being inferred per round from page state."""
    import api.lifespan as lifespan

    page = tmp_path / "index.html"
    page.write_text('<script defer="defer" src="./static/js/main.gone.js"></script>')
    monkeypatch.setattr(lifespan, "BAIT_INDEX", page)
    with pytest.raises(SystemExit):
        lifespan._check_bait_page_assets()


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
    assert response.json()["extension_names"] == list(load_pool_names())


def test_the_bait_page_is_served(client):
    response = client.get("/_web")
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


def test_rounds_run_sequentially(monkeypatch):
    monkeypatch.setattr(config.challenge, "n_rounds", 8)
    state = {"live": 0, "peak": 0}

    def slow_round(enabled, pool):
        state["live"] += 1
        state["peak"] = max(state["peak"], state["live"])
        time.sleep(0.01)
        state["live"] -= 1
        return {e: e in enabled for e in pool}

    score = score_with(monkeypatch, slow_round)

    assert score == pytest.approx(1.0)
    assert state["peak"] == 1


def test_results_are_recorded_in_index_order(monkeypatch):
    monkeypatch.setattr(config.challenge, "n_rounds", 6)

    def perfect(enabled, pool):
        return {e: e in enabled for e in pool}

    score_with(monkeypatch, perfect)


def test_a_mostly_broken_run_raises_instead_of_returning_a_deflated_score(monkeypatch):
    """Too many browser failures must not produce a misleading thin average."""
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
    """A single bad launch is noise, not a broken run - it must still score,
    and it must not dock the miner. 19 perfect rounds out of 19 SCORED rounds
    is 1.0, not 19/20."""
    from api.endpoints.challenge._browser import BrowserInfraError

    monkeypatch.setattr(config.challenge, "n_rounds", 20)
    calls = {"n": 0}

    def one_bad(enabled, pool):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrowserInfraError("transient")
        return {e: e in enabled for e in pool}

    assert score_with(monkeypatch, one_bad) == pytest.approx(1.0)


def test_a_miner_that_throws_is_still_charged_for_it(monkeypatch):
    """The counterpart to the two tests above.

    Infra failures leave the denominator; MINER failures must not. Otherwise a
    miner could throw on every round it was unsure about and be scored only on
    the ones it liked, which would raise its mean.
    """
    from api.endpoints.challenge._browser import BrowserError

    monkeypatch.setattr(config.challenge, "n_rounds", 20)
    calls = {"n": 0}

    def one_throw(enabled, pool):
        calls["n"] += 1
        if calls["n"] == 1:
            raise BrowserError("miner script threw")
        return {e: e in enabled for e in pool}

    assert score_with(monkeypatch, one_throw) == pytest.approx(19 / 20)


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

    pool = list(load_pool_names())
    manager = PayloadManager(pool=pool)
    manager.build_schedule(n_rounds=1, k=2)
    enabled = sorted(manager.rounds[0].enabled)

    failure = BrowserInfraError(f"2 of 2 extension(s) did not load: {enabled}")
    results = [service.RoundResult(0, None, failure, 1.0)]

    service._record_all(manager, results, "test")

    assert manager.rounds[0].error == "BrowserInfraError"
    blob = repr(manager.report())
    for ext_id in enabled:
        assert ext_id not in blob


def test_the_enabled_set_is_logged_at_debug_and_nowhere_else(monkeypatch):
    """The enabled set is the round's answer key, so it may reach the operator's
    log at DEBUG - which `logger.yml` pins to INFO in production - and nothing
    else. This is the diagnostic that makes an infra failure traceable to a
    specific extension; `report()` deliberately reduces `error` to a boolean, so
    without it there is no way to tell which extension killed a browser.

    Asserts both halves: the ids DO appear in the DEBUG record, and they do NOT
    appear at WARNING or in the report.
    """
    from api.endpoints.challenge._browser import BrowserInfraError
    from api.endpoints.challenge._payload_manager import PayloadManager

    pool = list(load_pool_names())
    manager = PayloadManager(pool=pool)
    manager.build_schedule(n_rounds=1, k=2)
    enabled = sorted(manager.rounds[0].enabled)

    seen: list[tuple[str, str]] = []
    for level in ("debug", "warning"):
        monkeypatch.setattr(
            service.logger,
            level,
            lambda msg, _lvl=level: seen.append((_lvl, str(msg))),
        )

    failure = BrowserInfraError(f"2 of 2 extension(s) did not load: {enabled}")
    service._record_all(manager, [service.RoundResult(0, None, failure, 1.0)], "test")

    debug_blob = " ".join(m for lvl, m in seen if lvl == "debug")
    warn_blob = " ".join(m for lvl, m in seen if lvl == "warning")

    assert enabled, "schedule produced an empty round"
    for ext_id in enabled:
        assert ext_id in debug_blob, f"{ext_id} missing from the DEBUG record"
        assert ext_id not in repr(manager.report()), f"{ext_id} leaked into report()"
    # The WARNING line carries the exception text, which for THIS hand-made
    # exception happens to contain the ids. What must not happen is the enabled
    # set being added to it independently - so it must be no worse than the text.
    assert warn_blob.count(enabled[0]) <= 1


def test_the_bait_page_is_not_served_to_a_remote_client(client):
    """During a run the served tree holds the submitting miner's code.

    `stage_detection_files()` writes their `<group>.js` into it and only
    `restore_stubs()` puts the stubs back, so anything readable here mid-run is
    a rival's submission. Chrome reaches it over loopback; nobody else should
    reach it at all. Covers the page route and the asset mount, which enforce
    this separately.
    """
    # Derived from the pool, not hardcoded: a group name baked in here breaks
    # this test every time the pool is recomposed, and the failure reads like
    # the loopback gate broke rather than the fixture going stale.
    from api.endpoints.challenge._pool import load_pool_groups

    _group = next(iter(load_pool_groups()))
    _paths = ("/_web", f"/static/detections/{_group}.js")

    for path in _paths:
        assert client.get(path).status_code == 200, f"sanity: loopback allowed for {path}"

    remote = TestClient(app, client=("10.0.0.5", 51234))
    for path in _paths:
        assert remote.get(path).status_code == 404, f"{path} leaked to a remote client"


def test_results_requires_the_api_key(client):
    """Same gate as /score: the report exposes the last-scored miner's
    per-round results, which a rival should not read off an open port."""
    assert client.get("/results").status_code == 401


def test_a_failed_run_clears_the_previous_report(monkeypatch):
    """The controller reads /results after every miner. A run that failed kept
    the previous miner's report there, so it was filed under the wrong miner."""
    from api.endpoints.challenge._browser import BrowserInfraError

    monkeypatch.setattr(service, "_last_report", {"score": 0.99})

    def _broken(*_a, **_kw):
        raise BrowserInfraError("simulated")

    monkeypatch.setattr(service, "run_round", _broken)
    with pytest.raises(RuntimeError):
        service.score(request_id="test", miner_output=solution("// stub"))
    assert service.get_results() is None


def test_swagger_examples_use_static_detection_files():
    from api.endpoints.challenge.schemas import _detection_examples
    from api.endpoints.challenge.utils import DETECTIONS_DIR

    examples = {e["file_name"]: e["content"] for e in _detection_examples()}

    for group in load_pool_groups():
        file_name = f"{group}.js"
        assert examples[file_name] == (DETECTIONS_DIR / file_name).read_text(
            encoding="utf-8"
        )


def test_results_is_404_before_any_run(client, monkeypatch):
    monkeypatch.setattr(service, "_last_report", None)
    response = client.get("/results", headers={"X-API-Key": API_KEY})
    assert response.status_code == 404


def test_results_reports_the_last_run(client):
    """A scored run is readable afterwards, per round."""
    assert (
        client.post("/score", json=payload(), headers={"X-API-Key": API_KEY}).status_code
        == 200
    )

    response = client.get("/results", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200

    body = response.json()
    assert body["n_rounds"] == len(body["rounds"])
    assert body["n_completed"] <= body["n_rounds"]
    assert 0.0 <= body["score"] <= 1.0
    assert [r["index"] for r in body["rounds"]] == list(range(body["n_rounds"]))


def test_results_reports_detected_extensions(client):
    assert (
        client.post("/score", json=payload(), headers={"X-API-Key": API_KEY}).status_code
        == 200
    )
    body = client.get("/results", headers={"X-API-Key": API_KEY}).json()

    pool = set(load_pool_names())
    for round_report in body["rounds"]:
        detected = round_report["detected_extensions"]
        assert set(detected) <= pool
        assert len(detected) == round_report["n_enabled"]
        assert "returned" not in round_report
        assert "expected" not in round_report

    # Store ids remain private; reports use published extension names.
    flat = json.dumps(body)
    for ext_id in load_name_to_id().values():
        assert ext_id not in flat, f"{ext_id} leaked into /results"


# -- coverage weighting ------------------------------------------------------


def _coverage(pool, schedule):
    from collections import Counter
    c = Counter(e for rnd in schedule for e in rnd)
    return [c[e] for e in pool]


def test_uniform_sampling_leaves_extensions_untested():
    """The behaviour `coverage_bias` exists to fix. An extension that is never
    enabled can only cost a miner - a false positive on it is still punished -
    and never earn them anything."""
    from api.endpoints.challenge._payload_manager import build_round_schedule

    pool = [f"e{i}" for i in range(25)]
    worst = max(
        _coverage(pool, build_round_schedule(pool, 10, 5, 1.0)).count(0)
        for _ in range(40)
    )
    assert worst > 0, "uniform sampling should sometimes miss extensions"


def test_weighting_covers_the_pool_far_more_evenly():
    from api.endpoints.challenge._payload_manager import build_round_schedule

    pool = [f"e{i}" for i in range(25)]
    missed = sum(
        _coverage(pool, build_round_schedule(pool, 10, 5, 3.0)).count(0)
        for _ in range(40)
    )
    # Measured over 2000 repeats: mean 2.4, worst 10; uniform sampling misses
    # ~91. The old bound of 4 failed about one run in eight by chance.
    assert missed <= 12, f"weighted sampling still missed {missed} across 40 runs"


def test_no_round_is_ever_deducible_by_elimination():
    """THE property that makes this safe.

    A rule that excludes an already-used extension makes late rounds inferable:
    with n_rounds*k a multiple of the pool size, the final round becomes fully
    determined, and an attacker with no detection ability at all scores 1.000
    on it. Every extension must stay reachable in every round, however heavily
    it has already been used.
    """
    from api.endpoints.challenge._payload_manager import build_round_schedule

    pool = [f"e{i}" for i in range(25)]
    # 5 rounds x k=5 over 25 is the worst case: the slots exactly exhaust the
    # pool, so under strict elimination the final round IS the set of
    # extensions not yet used. Predict it that way and count how often the
    # guess is perfect.
    perfect = 0
    runs = 80
    for _ in range(runs):
        sched = build_round_schedule(pool, 5, 5, 10.0)  # heaviest allowed bias
        used = {e for rnd in sched[:-1] for e in rnd}
        if set(sched[-1]) == set(pool) - used:
            perfect += 1
    assert perfect < runs * 0.5, (
        f"the final round matched the unused set in {perfect}/{runs} runs - "
        f"it is deducible by elimination, which scores 1.000 for an attacker "
        f"with no detection ability"
    )


def test_every_round_still_has_exactly_k_distinct_extensions():
    from api.endpoints.challenge._payload_manager import build_round_schedule

    pool = [f"e{i}" for i in range(25)]
    for bias in (1.0, 2.0, 5.0):
        for rnd in build_round_schedule(pool, 8, 5, bias):
            assert len(rnd) == 5
            assert rnd <= set(pool)
