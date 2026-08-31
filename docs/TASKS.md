# Tasks

Working backlog. Each issue states **why** it matters and what **evidence** it rests
on, so nothing here is a matter of taste. Tick them off as they land.

Status legend: `[ ]` open · `[x]` done · `[?]` needs a decision from Bekbolot

---

## Phase 0 — Land what already exists

26 files of finished, tested work sit uncommitted on `feat/detections-placeholder`.
Everything below builds on it, so it goes in first.

### 0.1 `[ ]` Commit the branch

**Why:** the work is done and verified (103 tests green, clean-image build scored
0.1646, full loop verified end to end). Leaving it uncommitted risks another reset
like the one that already happened once today.

**Contents:** detections-placeholder execution model, `service.py` refactor
(`score()` 96 → 59 lines), `auth.py` dedup, `settle_seconds` fix, the activity
audit script, 11 new tests.

**Acceptance:** `git log` shows the work; `pytest` green on a fresh checkout.

### 0.2 `[ ]` Decide what to do with `solution.js.stub`

**Why:** it is staged for commit but is a **runtime artifact** — written by
`stage_detection_files()` each run. Committing it means every scoring run dirties
the tree.

**Deferred by request.** Do not touch `templates/static/` until this is picked up
deliberately. Options when we get there: gitignore it, or drop the backup file
entirely and restore from the in-memory copy `schemas.py` already reads.

Two more findings from the API review belong with this work:

- **The detections path is computed twice** — `schemas.py` (`_STUB_PATH`) and
  `utils.py` (`DETECTIONS_DIR`) each derive
  `templates/static/detections` independently. Two sources of truth for one path.
- **`_STUB_CONTENT` is read at import.** If the app boots while a miner's file is
  staged — a crash mid-run — then `/openapi.json` publishes **that miner's
  submission** as the schema example. Low probability, real leak path.

---

## Phase 1 — Correctness

Bugs with evidence. These change scores today.

### 1.1a `[x]` A dead browser was blamed on the miner — **done**

`run_script()` called `execute_async_script` bare, so a dead renderer escaped as a
raw `WebDriverException` — which is not in our exception hierarchy at all:

```text
isinstance(WebDriverException("tab crashed"), BrowserInfraError) = False
isinstance(WebDriverException("tab crashed"), BrowserError)      = False
```

`_record_all()` classifies with `isinstance(err, BrowserInfraError)`, so every
renderer death was booked against **the miner** and `_MAX_SETUP_FAILURE_RATIO`
could never fire for the one failure mode it was built for. Measured: at P=4,
3 of 4 rounds died as `tab crashed` and `/score` still returned `0.165`.

`run_script()` now wraps the call and re-raises as `BrowserInfraError`, matching
what `open_page()` already did. The catch is unconditionally safe because
`wrap_miner_script` converts every miner-side failure into a payload
(`__error` / `__timeout`), so nothing thrown here is ever the submission.

Pinned from **both** sides — one-sided coverage would let it drift back:

- `test_a_dead_renderer_is_our_fault_not_the_miners` — `WebDriverException` →
  `BrowserInfraError`
- `test_a_broken_submission_stays_the_miners_fault` — timeout / throw / bad shape
  stay plain `BrowserError`, asserting `not isinstance(..., BrowserInfraError)`

The first was proved to fail against the pre-patch code before the fix was
restored.

### 1.1b `[ ]` `interact()` still swallows a dead browser

**Why:** it catches `WebDriverException`, the base class of Selenium's whole
exception hierarchy — so `InvalidSessionIdException` (browser is gone) is recorded
as "skipped" alongside a merely missing element. The round marches through all six
gestures before anything notices.

**What 1.1a did and did not change.** `InvalidSessionIdException` and
`NoSuchWindowException` both subclass `WebDriverException`, so a browser that dies
during gestures now surfaces from `run_script()` as `BrowserInfraError` and is
scored correctly. **The blame bug is closed.** What remains is fail-fast and
diagnostics: the round still burns six gesture timeouts before failing, and the
message says `browser died running the script` rather than naming the gesture that
killed it.

**Evidence:**

```text
signature script   OK
interact()         OK   ['click #accept-cookies -> skipped (InvalidSessionIdException)', …]
signature again    CRASH  Message: invalid session id
```

**Fix:** catch `InvalidSessionIdException` / `NoSuchWindowException` *before* the
broad clause and re-raise as `BrowserInfraError`.

**Scope:** `_browser.py`, ~6 lines, plus one test.

### 1.2 `[ ]` Bitwarden reproducibly kills the browser

**Why:** not merely undetectable — it appears to destabilise any round it is in.

**Evidence:** two audit runs and one isolation run all died with `invalid session
id`, always at the first gesture. It launches fine and survives 8s on the page, so
the trigger is in the gesture phase. Root cause unknown.

**Blocked by:** 1.1b — the round is now *scored* correctly, but `interact()` still
swallows the death, so we still cannot see which gesture kills it.

**Acceptance:** either a root cause, or Bitwarden moves to `rejected:` (see 2.1).

### 1.3 `[ ]` Shipped config carries laptop values

**Why:** `templates/configs/challenge/challenge.yml` is the **production default**,
and it currently reads `n_rounds: 5`, `k_min: 5`, `k_max: 5`,
`max_parallel_rounds: 1`. Local tuning leaked into the shipped file.

`k_min == k_max` is the damaging one: with `k` fixed, cardinality stops being part
of the prediction, precision and recall are forced equal, and **false positives stop
costing anything**. That is a scoring property we deliberately built and it is
currently off.

**Fix:** restore `n_rounds: 20`, `k_min: 3`, `k_max: 8`, `max_parallel_rounds: 4`
in `templates/`. Leave `volumes/` tuned for the laptop.

**Acceptance:** the two files differ, and the difference is intentional and noted.

### 1.4 `[x]` `settle_seconds` too low — **done**

Was `4.0`, now `6.0` in all three places (pydantic default + both config copies).

**Measured** by bisecting each blocker's detection threshold, one browser, idle box:

| extension | 3s | 4s | 5s | 6s |
| --- | --- | --- | --- | --- |
| Adblock Plus | ✗ | ✓ | ✓ | ✓ |
| Privacy Badger | ✓ | ✓ | ✓ | ✓ |
| **DuckDuckGo** | ✗ | **✗** | ✓ | ✓ |

At the old `4.0`, **DuckDuckGo was invisible in every round it appeared in** — a
permanent false negative on one of the 27. Adblock Plus and Privacy Badger were
fine; an earlier note here claimed all three were affected, which the bisect
disproved.

`6.0` rather than `5.0` because 5s is the exact threshold on an idle box running
one browser, and the shipped config runs four concurrently. Cost: ~2 min of waiting
per 20-round run.

### 1.5 `[ ]` A tolerated infra failure silently costs the miner 20%

**Why:** `calculate_score()` divides by **every** round, failed ones included:

```python
return sum(rec.score for rec in self.rounds) / len(self.rounds)
```

At the shipped `n_rounds=5` the guard's threshold is `0.2 × 5 = 1.0`, and `1 > 1.0`
is False — so exactly one infrastructure failure is **tolerated**, and a tolerated
failure caps a perfect miner at `4/5 = 0.80`. The case the guard deliberately lets
through is a silent 20% haircut for our bug.

Two more problems with the shape:

- The effective tolerance is not 20%. It is 100% at `n_rounds=1`, 33% at 3, 25% at
  4, 40% at 5 — and at 5 it lands *exactly* on the float boundary, so changing the
  constant to `0.19` silently flips the policy from "tolerate one" to "tolerate
  none".
- The name and comment say "browsers failing to **start**", but since 1.1a
  `BrowserInfraError` also covers navigation and renderer death.

**Fix:** drop infra failures from the denominator instead of averaging them in.

| Failure | Score | In denominator? |
| --- | --- | --- |
| Miner's JS threw / hung / wrong shape | 0 | **yes** |
| Browser died (`BrowserInfraError`) | — | **no** |

The asymmetry is load-bearing, not arbitrary. Miner failures **must** stay in the
denominator: otherwise a miner could throw on every round it was unsure about and
be scored only on the easy ones, which would raise its mean. Excluding *our*
failures costs the miner nothing and merely shrinks the sample — which turns the
ratio guard's job into the honest question its name already asks, "did enough
rounds run to mean anything?"

**Depends on:** 1.1a, which is what makes the classification trustworthy.

---

## Phase 2 — Pool integrity

Can every label actually be earned?

### 2.1 `[?]` Six extensions produce no observable signal

**Why:** a label nobody can earn is noise that caps achievable MCC for reasons
unrelated to skill.

**Evidence** — `scripts/audit_activity.py`, each launched alone against a
no-extension baseline:

| verdict | n | extensions |
| --- | --- | --- |
| both WAR + behaviour | 9 | LastPass, 1Password, Dashlane, NordPass, Grammarly, LanguageTool, Immersive Translate, Evernote, Loom |
| WAR only | 8 | Honey, Capital One, Rakuten, ProWritingAid, Turn Off the Lights, Google Translate, ColorZilla, Wappalyzer |
| behaviour only | 4 | Adblock Plus, Privacy Badger, DuckDuckGo, Dark Reader |
| **neither** | **6** | **Ghostery, Bitwarden, Notion Clipper, JSON Formatter, Video DownloadHelper, Checker Plus** |

Ghostery is inert by construction — it ships all 33 blocking rulesets with
`enabled=False` until onboarding completes.

**Decision needed:** move the 6 to `rejected:`, or seed profiles to wake them.
Profile seeding is proven mechanically viable (storage persists across launches
because our ids are stable), but needs per-extension onboarding automation that
breaks on every extension update.

**Recommendation:** reject. A pool of 21 that can all be earned beats 27 where 6
cannot.

### 2.2 `[?]` The pool never changes — the comparison gate zeroes everyone

**Why:** fixed pool → fixed resource paths → one sensible detector → near-identical
submissions. `controller.py` then does:

```python
if _higest_comparison_score >= self.comparison_min_acceptable_score:
    _scoring_log.score = 0.0
```

Two honest miners doing correct work independently both score nothing.

**Ruled out:** stripping `web_accessible_resources` to remove the shortcut. The
audit killed it — 8 extensions are detectable *only* by their WAR file, so
stripping would take detectable from 21 to 13.

**Decision needed:** rotation policy. E.g. publish a random 15 of 40 per epoch, or
rotate on a schedule. `_pool.py` then implements it.

**This is the biggest open decision on the project.**

---

## Phase 3 — Subnet registration

Nothing runs in production until these land.

### 3.1 `[ ]` Rename `MyController` / `MyChallengeManager`

**Why:** still the template's placeholder names. The siblings are `ABSController`
and `BVController`; ours is the only one that does not say what it is — and
`active_challenges.yaml` has to reference it by dotted path.

**Scope:** `controller.py`, `challenge_manager.py`, their `__all__`.

### 3.2 `[ ]` Write the `active_challenges.yaml` entry

**Why:** this is the **only** path by which `compose.yml`'s hardening reaches
production — `cap_drop`, `tmpfs …,exec`, `shm_size`, `pids_limit`, `security_opt`.
Without it prod runs with none of that.

**Evidence:** `grep extension active_challenges.yaml` → 0 matches. We are not
registered at all.

**Needs:** `challenge_image`, `target`, `script_path_identifier: "commit_files"`,
`challenge_type: "exc"`, `scoring_headers`, `comparison_config`,
`challenge_container_run_kwargs`, `protocols`.

### 3.3 `[ ]` Register the submodule and `challenge_type`

**Why:** `.gitmodules` lists only `ab_sniffer` and `bot_virus`. `challenge_type`
builds the `/check/challenge/{type}/` validation URL, which needs an `exc` type
server-side.

---

## Phase 4 — Hardening and cleanup

Real, none urgent.

### 4.1 `[ ]` Docker Desktop → 8 GB

**Why:** VM is 3.813 GB. Worst-case round stages 853 MB to tmpfs (which is RAM)
plus ~1 GB of Chrome. This is what causes `tab crashed`.

### 4.2 `[ ]` Cut tmpfs staging cost

**Why:** `/opt/extensions` is 1.2 GB; Adblock Plus alone is 312 MB, copied every
round it appears in. Prune unused assets at build time, or hardlink where DNR is
not involved.

**Acceptance:** worst-case round staging under ~300 MB.

### 4.3 `[ ]` MCC clamp is per-round

**Why:** `max(0.0, mcc(...))` folds negative correlation up to zero *before*
averaging, so a zero-information guesser scores ~0.08. That is bias, not variance —
more rounds does not remove it. Clamping the run mean instead lets negative rounds
cancel positive ones.

### 4.4 `[ ]` `extra="allow"` hides config typos

**Why:** a misspelled key — or a stale `n_trials:` from before the rename — is
silently accepted and ignored, falling back to the default. No error.

**Fix:** `extra="forbid"`, or log resolved values at startup.

### 4.5 `[ ]` Pin-rot workflow

**Why:** NordPass and ProWritingAid both drifted within two days and broke the
build at a moment nobody chose. `screen_extensions.py` already detects it; it wants
a scheduled run that opens a PR instead.

### 4.6 `[ ]` `.env` parsing in `run_local_challenge.py`

**Why:** hand-rolled string split. Breaks on `export FOO=`, quotes, comments.

### 4.7 `[ ]` Docs are stale

**Why:** `BUILD.md` references scripts that were deleted (`validate_selenium.py`,
`smoke_chrome.py`, `dev_round.py`, `_harness.py`). `design.md` and `README.md` were
mechanically renamed `trial` → `round` and never re-read for sense.

---

## Phase 5 — Code-quality review findings

From a structural audit of the uncommitted branch (2026-09-01). Ordered by
conviction, not by size.

### 5.1 `[ ]` `run_round.py` re-implements `utils.py`

**Why:** `_stage_miner_js()` / `_restore_stub()` are a hand copy of
`stage_detection_files()` / `restore_stubs()`, and the docstring concedes it —
*"this is the dev-tool copy so run_round.py keeps working from a checkout, where
the app config … may not import."*

**That reason does not hold.** `utils.py`'s only config coupling is
`from .schemas import MinerOutput`, and it uses exactly two attributes,
`.file_name` and `.content`. `api.logger` is a two-line re-export of
`beans_logging_fastapi` with no config at all.

The copies have **already diverged**: the dev copy has no path-traversal guard,
catches `OSError` where the canonical one catches `Exception`, uses
`with_suffix(".js.stub")` vs `with_suffix(suffix + ".stub")`, and silently leaves
miner code on disk where the canonical one unlinks it.

**Fix:** have `stage_detection_files()` take `Mapping[str, str]` instead of
`MinerOutput`. The staging layer stops knowing the HTTP schema — a layer leak in
its own right — and `run_round.py` imports the canonical helper, which already
accepts a `detections_dir`. Deletes ~26 lines.

### 5.2 `[ ]` The staging redesign's rationale is recorded nowhere

**Why:** ~200 lines (`utils.py`, two stub files, a `finally`, a path guard, a
91-line test) replaced one line of `{miner_js}` injection. The one thing that buys
is **early load** — the detector runs before extensions act, so it can install
`MutationObserver`s and hook `fetch`/`XHR` rather than only reading end state.

That is a real capability and the redesign is right. But nothing exercises it (the
baseline only assigns `window.detect_extensions`, invoked ~7s later) and nothing
states it — `index.html`'s comment argues the *opposite*: "It only DEFINES
window.detect_extensions — the challenge calls it after the settle window, never at
load." The next maintainer reverts this.

**Fix:** a comment, not a revert. §6 of REFERENCE.md now carries part of it.

### 5.3 `[ ]` `audit_activity.py` is not in the image

**Why:** its own docstring says
`docker compose exec challenge-api python3 /usr/local/bin/audit_activity.py`. The
Dockerfile copies only `scripts/*.sh` and `run_round.py` there (lines 252–253). This
is the tool that produces `time_to_stable_ms`, the blocking input for pool curation.

### 5.4 `[ ]` The baseline's hardcoded pool is already stale

**Why:** `solution.js` hardcodes **27** ids; `extensions.yml` has **29**.
`detect_extensions()` takes no arguments, so every miner is *forced* to hardcode a
list the challenge owner can change underneath them.

**Fix:** have the bait page set `window.CHALLENGE_POOL` from `load_pool_ids()`. The
full pool is already public via `/task`, so this leaks nothing, and the hardcoded
block deletes itself.

### 5.5 `[ ]` Three byte-identical `solution.js`, and a dead backup branch

`templates/static/detections/solution.js` ≡ `solution.js.stub` ≡
`examples/miner_commit/src/commit/solution.js`.

Because `.stub` is committed, `if target.is_file() and not backup.exists()` is
**always false** in a real deployment — `shutil.copy2(target, backup)` is
unreachable. If it ever did fire (someone deletes `.stub`) it would promote **a
miner's submission** to "the canonical stub", permanently.

Overlaps 0.2 — resolve them together.

### 5.6 `[ ]` Small, mechanical

- `restore_stubs(staged, detections_dir=DETECTIONS_DIR)` — `detections_dir` is
  never used in the body.
- `index.html` comment names `copy_detection_files()`. No such function; it is
  `stage_detection_files()`.
- `run_round.py` `k = min(args.k, len(ids))` can produce `k == len(ids)`, which
  `build_round_schedule` now *raises* on. Should be `len(ids) - 1`.
- `_reject()`'s docstring claims every rejection is identical except `reason`;
  `message` differs too.
- `black` would reformat 8 files, 6 of them in this branch.

### 5.7 `[ ]` Housekeeping

- `merged.csv` (20 KB) is untracked at the repo root — decide: commit, move under
  `docs/`, or delete.
- Two worktrees still on disk: `.claude/worktrees/review-fixes` (content ported)
  and `.claude/worktrees/pool-candidates` (locked, 29 ids).

---

## Deliberately not doing

- **Refactor `_browser.py` / `_payload_manager.py`** — measured: no function over
  32 lines, cohesive, already sectioned by phase. Changing them would be churn.
- **Strip `web_accessible_resources`** — the audit disproved it (see 2.2).
