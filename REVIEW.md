# API & Backend Architecture Review — Extension Classification Challenge

## Phase 2 note

No clarification is required; the review below proceeds from the available source.

## 1. Executive summary

The scoring pipeline is well-structured for a single-process, single-worker deployment: ground truth is generated with `secrets.SystemRandom()`, `miner_input` is deliberately dropped, single-flight scoring is enforced with a `threading.Lock`, and each round gets its own tmpfs scratch directory and process sweeper. The test suite passes in the checked-out tree (92 passed, 1 `starlette.testclient` deprecation warning), and black/flake8/bandit are clean on the target files.

The high-confidence invariant issue has been fixed to the chosen trade-off: **server logs no longer contain the exact enabled subset**. `BrowserInfraError` now logs only the IDs of extensions that *failed* to load (`missing`), never `sorted(self.loaded)`; `RuntimeError` and `RoundRecord.error` now carry a safe class-name summary instead of the raw ID-bearing exception text; and `RoundRecord.error` is redacted against the pool before storage. The server log still contains failed-only IDs for build/config debugging, and those logs are still bind-mounted out of the container by `active_challenges.yaml` (`${RT_CHALLENGE_LOGS_DIR}:/var/log/rest-abs-challenge`).

`pyright` is not configured in the repo (`[tool.pyright]` is commented out in `pyproject.toml`) and only emits `api.*` import-resolution noise; it was not used as evidence. Pre-commit's `detect-secrets`, `shellcheck`, and `markdownlint` hooks fail on out-of-scope files (extension public keys, deployment scripts, stale docs) and were skipped for the target code.

### Decision note: server logs and the enabled subset (implemented)

The chosen policy is **option 2 (failed-only IDs)** plus sanitisation of every other surface:

- `BrowserInfraError` raised in `_browser.py:launch` contains only `sorted(missing)` — the extensions requested but not loaded — and never `sorted(self.loaded)`.
- `service.py:logger.warning` still receives the full `BrowserInfraError` text, so failed IDs remain in the server log for build/config debugging.
- `service.py` now stores only `f"{type(_err).__name__}"` in `RoundRecord.error` and in the `RuntimeError` message, so neither surface carries the loaded set or the full failed-only list.
- `_payload_manager.py` redacts every extension ID from `error` before it is stored on `RoundRecord.error`, as a defence-in-depth layer.

This keeps the subset that *loaded* out of exception messages, the dataclass, and the `RuntimeError` traceback, while preserving the ability to see which extensions failed to load in the bind-mounted server log.

## 2. Architecture map

```text
GET /task
  router.py:get_task
    service.py:get_task
      _pool.py:load_pool_ids   -> cached tuple of pool ids from extensions.yml
    response: MinerInput(extension_ids=<pool>, random_val=<uuid-like>)

POST /score
  router.py:post_score
    auth_api_key (X-API-Key)
    _scoring_lock (threading.Lock, process-local; uvicorn must run single-worker)
    service.py:score
      _pool.py:load_pool_ids
      _payload_manager.py:PayloadManager.build_schedule  # ground truth subsets
      BrowserSettings from config.challenge.browser
      ThreadPoolExecutor(max_workers=min(max_parallel_rounds, n_rounds))
        _run_one_round -> _browser.py:run_round
          ChromeSession.__enter__  -> mkdir scratch root/profile/ext
          ChromeSession.launch     -> copy extensions, start Chrome, prove load
          ChromeSession.open_page  -> GET 127.0.0.1:<port>/_web/index.html
          ChromeSession.interact   -> fixed gestures on bait page
          ChromeSession.run_script -> execute_async_script(wrap_miner_script(...))
          normalize_predictions    -> one bool per pool id
          ChromeSession.close      -> driver.quit, killpg, rmtree scratch
      record each round, check setup-failure threshold, calculate mean MCC
    response: float in [0, 1]
```

State/resource ownership:

- `load_pool_ids()` is cached once per process.
- `PayloadManager` is created per `/score` call; it owns `RoundRecord.enabled` (the ground truth).
- `ChromeSession` owns the per-round scratch directory and the Chrome process; teardown runs in `__exit__`.
- `_scoring_lock` is a module-level `threading.Lock`, so it serialises calls only within one process.
- `ThreadPoolExecutor` worker threads are internal to `service.score`; I/O and sleeps happen there, not on the event loop.

Validation: `MinerOutput` schema enforces a single file named `solution.js` and a line limit. Business logic (MCC, schedule, scoring) lives in `_payload_manager.py`. Browser I/O and process management live in `_browser.py`. Errors are caught at round granularity in `_run_one_round`, at request granularity in `router.py`, and at the app level by template handlers.

## 3. Invariant verification

The enabled subset must not be recoverable from the browser, the response, error bodies, logs, Chrome surfaces, the scratch path, or process argv.

| Path | Status | Evidence |
| --- | --- | --- |
| Bait page & static assets (`/_web/index.html`) | **NO LEAK** | `src/exc_challenge/challenge/templates/index.html` is static HTML with fixed ad divs, form fields, and canvas. No server-rendered value, no query params, no injected variables. |
| `/score` response body | **NO LEAK** | `router.py:92` returns `_score` (a float). `PayloadManager.report()` and `RoundRecord.as_public_dict()` are not returned to the client. |
| `/score` error responses | **NO LEAK** | `router.py:85-88` raises `BaseHTTPException` with a generic `message="Failed to score the miner output!"`. The response `detail` does not include the original `RuntimeError` text. |
| Server logs (`logger.warning` / `logger.exception`) | **PARTIAL LEAK (chosen trade-off)** | `_browser.py:launch` now builds `BrowserInfraError` with `sorted(missing)` only; `service.py:120-130` logs `_err` for failed-only debugging. `RuntimeError` no longer embeds the raw text, and `RoundRecord.error` is sanitised. The bind-mount to `${RT_CHALLENGE_LOGS_DIR}` still lets failed-only IDs leave the container. |
| Chrome surfaces (`chrome://extensions-internals/`, `window.chrome`, DevTools) | **NO LEAK** | The miner script runs on `http://127.0.0.1/_web`, same-origin/cross-scheme from `chrome://`. Individual extensions can inject content scripts, but they only learn their own ID, not the enabled set. |
| Tmpfs scratch path & contents | **NO LEAK** | `ChromeSession.__init__` uses `Path(settings.scratch_dir) / f"round-{tag}"` where `tag` is `<request_id>-<index>`. The `request_id` is a UUID or header; the path is random per round. Contents are extension copies. Not reachable from the renderer sandbox. |
| Process argv (`--load-extension=...`) | **NO LEAK** | The Chrome command line does contain absolute paths ending in `ext/<id>`, but `/proc/<pid>/cmdline` is OS-level, not reachable from browser JS. `_sweep_processes` matches on the scratch root path, not on IDs. |
| HTTP access logs | **NO LEAK** | `beans_logging_fastapi` middleware logs method, path, status, size, and timing; it does not log request/response bodies. |
| Timing side channels | **NO LEAK under `k_min == k_max`** | With fixed `k=5` (default `k_min=k_max=5`), copy/script timing cannot reveal subset cardinality. If `k` is widened, total run time could correlate with `n_enabled`. |
| `RoundRecord` / `PayloadManager.report()` | **NO LEAK** | `RoundRecord.as_public_dict()` still exposes `n_enabled` and per-round `failed` flags, but `PayloadManager.report()` is not returned or logged. `RoundRecord.error` is now redacted against the pool before storage, so it cannot carry the answer key. |
| `miner_input` | **NO LEAK** | `router.py:61` accepts `miner_input` but `service.py:score` does not consume it. The enabled subset is drawn independently by `PayloadManager`. |
| Random subset generation | **NO LEAK** | `_payload_manager.py:91` uses `secrets.SystemRandom().sample`. Subsets are kept in `RoundRecord.enabled` and not serialized. |
| `wrap_miner_script` / `run_script` | **NO LEAK** | `_browser.py:114-140` wraps the miner code with `budget_sec` and `window.detect_extensions`; the enabled subset is never injected into the page or script. |

## 4. Bugs & logical gaps

### 1.1 Server logs can contain the exact enabled subset (fixed to failed-only)

- **Severity:** High (mitigated to the chosen failed-only trade-off)
- **Confidence:** High
- **Files:** `_browser.py:191-198`, `_browser.py:207-217`, `service.py:120-130`, `service.py:142-150`, `router.py:83-84`, `_payload_manager.py:104-119`

**Evidence:**

```python
# _browser.py:191-198
missing = set(ext_ids) - self.loaded
if missing:
    # REFERENCE §3: a mis-keyed extension is otherwise a permanent silent
    # false negative, capping MCC for a reason no miner can fix.
    # Log only the IDs that failed to load; the loaded set is the round's
    # answer key and must not leave the Python process.
    raise BrowserInfraError(
        f"extension(s) not loaded, or ids drifted: {sorted(missing)}"
    )
```

```python
# _browser.py:207-217
if "," in ext_id:
    raise BrowserInfraError(
        f"id '{ext_id}' has a comma; --load-extension splits on it"
    )
...
if not src.is_dir():
    raise BrowserInfraError(
        f"'{ext_id}' not unpacked under {source_root} - "
        f"run scripts/fetch_extensions.py"
    )
```

```python
# service.py:120-130
if _err is not None:
    # The full exception is logged below for failed-only debugging;
    # RoundRecord.error and the RuntimeError get a safe summary instead.
    _error = f"{type(_err).__name__}"
    if isinstance(_err, BrowserInfraError):
        _setup_failures += 1
        logger.warning(
            f"[{request_id}] - Round {_index} could not start: {_err}"
        )
```

```python
# router.py:83-84
except Exception:
    logger.exception(f"[{_request_id}] - Failed to score the miner output!")
```

```python
# _payload_manager.py:104-119
"""Report shape. Contains no ground truth, so it is safe to serialise.

`error` is deliberately reduced to a flag. Raw browser messages name the
extensions they were trying to load and can be this round's answer key;
any stored text is redacted before it reaches `self.error`.
"""
```

**Problem (before fix):** `BrowserInfraError` messages carried the IDs selected for the round (`missing`) and the IDs Chrome actually loaded (`loaded`). Because `ext_ids == sorted(subset)`, `missing ∪ loaded == enabled` whenever the exception was raised. Those messages were written to the server log by `logger.warning` in `service.py` and by `logger.exception` in `router.py` via the `RuntimeError` that embedded `Last error: {_error}`. `RoundRecord.error` also stored the raw text. Because logs are bind-mounted to the validator host, the ground truth left the container.

**Why it matters:** This was the core invariant violation. Anyone with access to the validator's log directory could reconstruct the enabled subset for the round.

**Reachability:** Triggered by any setup failure. The loaded set no longer appears in exception messages; only `missing` IDs remain in the `logger.warning` failed-only log.

**Applied fix (middle path):**

- `_browser.py:launch` now logs only `sorted(missing)`; `sorted(self.loaded)` has been removed.
- `service.py` now sets `_error = f"{type(_err).__name__}"` for `RoundRecord.error` and `RuntimeError`; the full `BrowserInfraError` is still passed to `logger.warning` for failed-only debugging.
- `_payload_manager.py` redacts extension IDs from any `error` string before storing it on `RoundRecord.error`.

**Behaviour change:** Log output no longer contains the loaded set; `RuntimeError` and `RoundRecord.error` now contain a class-name summary. Response bodies and scores are unchanged.

### 1.2 `RoundRecord.error` now redacts extension IDs (fixed)

- **Severity:** Medium (resolved)
- **Confidence:** High
- **Files:** `_payload_manager.py:95-119`, `service.py:132-137`

**Evidence (after fix):**

```python
# _payload_manager.py:95-119
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

        `error` is deliberately reduced to a flag. Raw browser messages name the
        extensions they were trying to load and can be this round's answer key;
        any stored text is redacted before it reaches `self.error`.
        """
        return {
            "index": self.index,
            "status": self.status.value,
            "n_enabled": len(self.enabled),
            "score": round(self.score, 4),
            "failed": self.error is not None,
            "duration_sec": self.duration_sec,
        }
```

```python
# _payload_manager.py:129-144 (PayloadManager.__init__)
if self.pool:
    self._pool_id_re: re.Pattern[str] | None = re.compile(
        "|".join(
            re.escape(ext_id)
            for ext_id in sorted(self.pool, key=len, reverse=True)
        )
    )
else:
    self._pool_id_re = None
```

```python
# _payload_manager.py:152-169 (PayloadManager.record)
rec = self.rounds[index]
rec.duration_sec = duration_sec
rec.error = error
if rec.error is not None and self._pool_id_re is not None:
    rec.error = self._pool_id_re.sub("<id>", rec.error)
```

```python
# service.py:132-137
_payload_manager.record(
    _index,
    _predicted,
    error=_error if _err is not None else None,
    duration_sec=round(_elapsed, 2),
)
```

**Problem (before fix):** `RoundRecord.error` was populated with `str(_err)`, which could be the ID-bearing `BrowserInfraError` text. `as_public_dict()` correctly removed the `error` text, but the dataclass still carried it. Any future code that called `dataclasses.asdict(rec)`, `repr(rec)`, or serialised `report()` differently would leak the subset.

**Why it matters:** `RoundRecord` mixed the ground-truth `enabled` set with a raw `error` string that could contain the same IDs, making the public/private split fragile.

**Reachability:** Same setup-failure path as Finding 1.1; additionally, any accidental logging of `RoundRecord` or `PayloadManager.report()`.

**Applied fix:** `service.py` now passes only `f"{type(_err).__name__}"` into `record`, and `record` redacts every extension ID from the `error` string before storing it. `as_public_dict()` is unchanged.

**Behaviour change:** Internal `RoundRecord.error` contents are now a safe summary; public `as_public_dict()` shape is unchanged.

### 1.3 `RuntimeError` setup-failure threshold is sharp for small `n_rounds`

- **Severity:** Low
- **Confidence:** Medium
- **Files:** `service.py:142-150`, `_challenge.py:35-44`

**Evidence:**

```python
# service.py:142-150
if _setup_failures > _MAX_SETUP_FAILURE_RATIO * _n_rounds:
    raise RuntimeError(
        f"{_setup_failures} of {_n_rounds} round(s) failed to start a browser, "
        f"so the score would not be about the miner. If max_parallel_rounds "
        f"({_challenge_config.max_parallel_rounds}) was raised, lower it: "
        f"shm_size and mem_limit are shared across concurrent browsers. "
        f"Last error: {_error}"
    )
```

`_MAX_SETUP_FAILURE_RATIO = 0.2`, default `n_rounds = 20`. The test suite uses 20, where one failure does not trigger the threshold. With `n_rounds = 4`, a single setup failure satisfies `1 > 0.8` and aborts the whole run; with `n_rounds = 1`, any failure aborts.

**Problem:** The threshold is correct for the default 20 rounds, but it becomes very aggressive if the operator lowers `n_rounds` for testing or for a smaller validation window.

**Why it matters:** A single transient Chrome startup blip would cause a 500 for a small scoring window, leaving the validator with no score and no guidance on how many rounds actually ran.

**Reachability:** Only when `n_rounds` is reduced below the default.

**Recommended fix:** Either document that `n_rounds` should stay at or above 20, or make the threshold explicit (e.g. `max(2, floor(...))`) so very small runs are not aborted by one failure.

**Behaviour change:** Would change when 500 is returned for small `n_rounds`.

### 1.4 `service.py` `_error` string is also embedded in the `RuntimeError` message (fixed)

- **Severity:** Medium (resolved)
- **Confidence:** High
- **Files:** `service.py:142-150`, `router.py:83-84`

**Evidence (after fix):**

```python
# service.py:118-124
if _err is not None:
    # The full exception is logged below for failed-only debugging;
    # RoundRecord.error and the RuntimeError get a safe summary instead.
    _error = f"{type(_err).__name__}"
```

```python
# service.py:142-150
if _setup_failures > _MAX_SETUP_FAILURE_RATIO * _n_rounds:
    raise RuntimeError(
        f"{_setup_failures} of {_n_rounds} round(s) failed to start a browser, "
        f"so the score would not be about the miner. If max_parallel_rounds "
        f"({_challenge_config.max_parallel_rounds}) was raised, lower it: "
        f"shm_size and mem_limit are shared across concurrent browsers. "
        f"Last error: {_error}"
    )
```

`router.py:83-84` uses `logger.exception`, which prints the traceback including the `RuntimeError` message.

**Problem (before fix):** `RuntimeError` embedded the raw `_error` string, which could be the full ID-bearing `BrowserInfraError` text. `logger.exception` in `router.py` would then write that text to the log.

**Why it matters:** It multiplied the surfaces from which the subset could be recovered.

**Reachability:** Triggered whenever setup failures exceeded the 20% threshold.

**Applied fix:** `_error` is now `f"{type(_err).__name__}"`, so the `RuntimeError` and traceback carry only the exception class name.

**Behaviour change:** Log and exception message text no longer contain raw browser exception text; HTTP response is unchanged.

## 5. API contract problems

The only intended caller is `redteam_core/challenge_pool/controller.py`. The contract findings below are based on what that caller does in `src/exc_challenge/controller.py` and the bare-float `/score` shape used by `challenge_manager.py`.

### 2.1 Success and error response shapes differ

- **Severity:** Medium
- **Confidence:** High
- **Files:** `router.py:53-92`, `controller.py:54-64`

**Evidence:**

```python
# router.py:92
return _score
```

```python
# router.py:85-88
raise BaseHTTPException(
    error_enum=ErrorCodeEnum.INTERNAL_SERVER_ERROR,
    message="Failed to score the miner output!",
)
```

```python
# controller.py:54-64
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
```

**Problem:** `POST /score` returns a bare float on 200, but `BaseHTTPException` returns a `BaseResponse` JSON envelope (`{message, error}`) on 4xx/5xx. `controller.py` assigns the result directly to `_scoring_log.score`. If the base `_score_challenge` does not check HTTP status before parsing the body, a 500/429 response could be mis-parsed as a number or raise an opaque error downstream.

**Why it matters:** The controller must understand the dual contract. A 500 with a JSON body is not a float and must not be treated as a score.

**Reachability:** On every 4xx/5xx from `/score`.

**Recommended fix:** Document the contract for the controller (2xx = bare float; 4xx/5xx = `BaseResponse`). Consider returning a `BaseResponse` with `content=score` for consistency across all endpoints, though that is **BREAKING** for the controller.

### 2.2 Single-flight 429 requires the controller to retry

- **Severity:** Low
- **Confidence:** Medium
- **Files:** `router.py:66-73`

**Evidence:**

```python
# router.py:66-73
if not _scoring_lock.acquire(blocking=False):
    logger.warning(...)
    raise BaseHTTPException(
        error_enum=ErrorCodeEnum.TOO_MANY_REQUESTS,
        message="A scoring run is already in progress!",
    )
```

**Problem:** The endpoint returns 429 when a scoring run is already active. The controller is responsible for retrying; the endpoint does not queue. If the base controller does not handle 429, the miner receives no score for that task window.

**Why it matters:** The validator's scoring loop may be sequential, but if it is not, 429s will surface as failures.

**Reachability:** Whenever two `/score` requests overlap. Default `max_parallel_rounds = 1` and `n_rounds = 20` means a single `/score` call can take tens of seconds, so overlaps are possible under load.

**Recommended fix:** Document 429 semantics for the controller, or expose a queue/callback mechanism. No code change required if the controller already retries.

### 2.3 `/score` is long-running; controller timeout is unknown

- **Severity:** Low
- **Confidence:** Low
- **Files:** `service.py:96-114`, `_challenge.py:55-64`

**Evidence:**

```python
# service.py:96-114
_workers = max(1, min(_challenge_config.max_parallel_rounds, len(_payload_manager.rounds)))
...
with ThreadPoolExecutor(max_workers=_workers) as _executor:
    _results = list(
        _executor.map(
            lambda rec: _run_one_round(...),
            _payload_manager.rounds,
        )
    )
```

Default `n_rounds = 20`, `settle_seconds = 4.0`, `script_budget_sec = 10.0`, plus copy/launch/teardown. A single `/score` call can take well over 30 seconds.

**Problem:** If the controller's HTTP client uses a finite timeout shorter than the scoring runtime, it may disconnect while the server continues scoring and holds the lock. The user reports the validator's `_score_challenge` currently passes no timeout, so this is weak today.

**Why it matters:** It is a silent failure mode that looks like a scoring failure but is actually a client timeout.

**Reachability:** Only if the base controller starts using a finite timeout shorter than the scoring runtime.

**Recommended fix:** Document the expected `/score` duration and the recommended client timeout so future controller changes do not introduce timeouts.

### 2.4 Internal config validation errors become generic 500

- **Severity:** Low
- **Confidence:** High
- **Files:** `_payload_manager.py:82-89`, `service.py:75-79`, `router.py:83-88`

**Evidence:**

```python
# _payload_manager.py:82-89
if k_max >= len(pool):
    raise ValueError(
        f"k_max ({k_max}) must be < pool size ({len(pool)}); ..."
    )
```

```python
# service.py:75-79
_payload_manager.build_schedule(
    n_rounds=_challenge_config.n_rounds,
    k_min=_challenge_config.k_min,
    k_max=_challenge_config.k_max,
)
```

```python
# router.py:83-88
except Exception:
    logger.exception(...)
    raise BaseHTTPException(..., message="Failed to score the miner output!")
```

**Problem:** `build_schedule` raises `ValueError` for an invalid `k` range. The generic `except Exception` in `router.py` converts it to a 500 with the same message as any other server failure. The controller cannot distinguish a bad config from a transient infra error.

**Why it matters:** Misconfiguration should fail at startup or be reported as a client/config error, not a generic 500 mid-request.

**Reachability:** Only if `k_min`/`k_max` are misconfigured relative to the pool size. Default `k_max = 5` and pool size 27 makes this unreachable today.

**Recommended fix:** Validate `k_min`/`k_max` against the loaded pool at startup, or catch `ValueError` in `service.score` and re-raise as a distinct error code.

**Behaviour change:** Non-2xx status or error shape would change for misconfiguration.

## 6. Structural problems

- **Ground truth bleeds into the error model (fixed).** `RoundRecord` previously mixed the ground-truth `enabled` set with a raw `error` string. `service.py` now passes only a class-name summary into `record`, and `record` redacts any extension IDs before storage. The remaining structural concern is that `enabled` and `error` still live in the same dataclass; future refactors could separate ground truth from reporting state.

- **Error classification is scattered.** `service.py` decides whether an exception is "setup" or "round" by checking `isinstance(_err, BrowserInfraError)`. This couples orchestration to the exception hierarchy in `_browser.py`. If a future change introduces a new infra exception type, `service.py` will silently misclassify it. Centralising the "is this an infra failure?" decision inside `_browser.py` (or a small error taxonomy) would be cleaner.

- **`threading.Lock` scope is now documented.** `uvicorn` is launched by `bootstrap.py` with `**config.api.uvicorn.declared_dump()`; `UvicornConfig` does not declare `workers`, so `uvicorn.run` defaults to a single process. `router.py` now notes that the deployment must run with a single uvicorn worker because the `threading.Lock` is process-local. If multi-worker deployments become a requirement, the lock should be upgraded to a process-wide lock.

- **`MinerInput` has a dual role.** It is the response from `/task` and an ignored parameter of `/score`. This is intentional, but it means the same schema represents two different contracts. Splitting them would remove the ambiguity.

- **`PayloadManager.report()` is a latent leak surface.** It builds a per-round report with `n_enabled` and `failed`. It is not returned today, but its existence makes it easy to accidentally expose `n_enabled` later.

## 7. Cleanup

- `service.py:152-153` calls `_payload_manager.report()` only to read `n_completed` and `n_rounds`. `report()` recomputes the score and builds the full `rounds` list. Adding `n_completed`/`n_rounds` properties to `PayloadManager` would avoid that overhead.
- `service.py:118-137` now keeps the full exception for `logger.warning` and uses a safe class-name summary for `RoundRecord.error` and `RuntimeError`. The variable name `_error` no longer matches its content; renaming it to `_error_summary` would improve readability.
- `RoundRecord.as_public_dict()` comment in `_payload_manager.py:104-119` has been updated to reflect that stored error text is redacted.

## 8. What is already good

- Ground-truth randomness uses `secrets.SystemRandom()` and is never serialised into the page or response.
- `miner_input` is accepted and dropped, preventing a caller from shrinking the scored pool.
- `POST /score` is single-flight and the lock is released in a `finally` block.
- Each round gets an isolated scratch directory and profile; the process sweeper matches on the scratch root path, so concurrent rounds do not cross-contaminate.
- Extensions are copied to a writable tmpfs before load, avoiding read-only DNR silent failures.
- The sync `def` endpoint plus `ThreadPoolExecutor` keeps browser I/O off the asyncio event loop.
- `chrome://extensions-internals/` is used to prove that the requested extensions loaded.
- `wrap_miner_script` provides a clean async wrapper with timeout, error capture, and `window.detect_extensions` entrypoint.
- `_pool.py` excludes the `rejected` block and returns an immutable tuple.
- Authentication on `/score` uses constant-time comparison and the key is a `SecretStr`.

## 9. Top 5 actions (implemented)

1. **Log policy: failed-only IDs.** `BrowserInfraError` now logs only `sorted(missing)`, never `sorted(self.loaded)`. `service.py` `logger.warning` still receives the full `BrowserInfraError` for failed-only debugging; `RuntimeError` and `RoundRecord.error` receive only a safe class-name summary.
2. **Sanitise `RoundRecord.error`.** `service.py` now passes `f"{type(_err).__name__}"` to `record`, and `PayloadManager.record` redacts every extension ID from any `error` string before storing it on `RoundRecord.error`.
3. **Fix both leak paths together.** The `RuntimeError` message no longer embeds the raw ID-bearing `_error` string; `router.py` `logger.exception` therefore cannot leak the loaded set via the traceback.
4. **Confirm uvicorn worker count and document single-worker.** `UvicornConfig` does not expose `workers`; `bootstrap.py` calls `uvicorn.run` without it, so the default is a single process. `router.py` now documents that the `threading.Lock` is process-local and the deployment must run with one uvicorn worker.
5. **Review `PayloadManager.report()` / `RoundRecord` leak surface.** `report()` and `as_public_dict()` are unchanged and not returned to the client; `RoundRecord.error` is now redacted.

## 10. Conventions worth codifying

- **Ground truth must not appear in exception text or dataclass string fields.** Implemented for `BrowserInfraError`, `RuntimeError`, and `RoundRecord.error`: the loaded set is no longer logged, and `RoundRecord.error` is redacted. The remaining trade-off is the chosen failed-only log, which still leaves the container via the bind-mounted log directory.
- **Long-running endpoints must document 5xx/429 semantics and client timeouts.** `/score` is the only long endpoint; its contract with the controller should be explicit.
- **Single-flight concurrency must be process-wide if multi-worker deployments are possible.** Confirmed `uvicorn` defaults to one process here and the `threading.Lock` requirement is documented; upgrade to a process-wide lock if multi-worker is ever enabled.
