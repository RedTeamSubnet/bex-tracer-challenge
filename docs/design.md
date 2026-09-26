# Extension Classification Challenge (`exc`) — Design

> Design document and build plan for the `exc` challenge.
> Diagram: [`architecture.excalidraw`](./architecture.excalidraw) (open at excalidraw.com).

## Context

`challenges/extension-classification/` is a fresh cookiecutter render of `challenge-template`
(`module_name=bex_tracer`, `api_slug=rest-exc-challenge`, `env_prefix=BEX_`).
Everything is boilerplate — `service.py` currently returns `random.random()`.

We are building a RedTeam Subnet challenge where **miners submit JavaScript that fingerprints
which browser extensions are active in a Chrome session**. The harness launches Chrome with a
random subset of extensions from a published pool, loads a bait page, runs the miner's script
from page context, and scores how accurately it identified what was installed.

Real threat model: a malicious website fingerprinting a visitor's extensions. Unlike the sibling
challenges, **the browser driver lives in this repo** — plain Selenium, no bot-runner, no
pipeline dependency.

## Locked decisions

| Decision          | Value                                                          |
| ----------------- | -------------------------------------------------------------- |
| Threat model      | Page-context JS, injected post-load via `execute_async_script` |
| Browser driver    | **Selenium, in-repo**                                          |
| Extension pool    | ~30, IDs published to miners                                   |
| Enabled per trial | **Random k ∈ [3,8]**, subset never revealed                    |
| Sourcing          | `.crx` from the Chrome Web Store, downloaded at image build    |
| Metric            | **MCC** over all N binary decisions, `max(0, mcc)` → `[0,1]`   |
| Miner output      | `{extensionId: true\|false}` — plain booleans                  |
| Prod browser      | Chrome for Testing, **headless**                               |
| Dev browser       | Chrome for Testing, headful under Xvfb                         |

**Assumption:** script timeout → score whatever resolved, not a hard zero. Under MCC an empty
answer already scores ~0, so the harsh rule buys nothing and would punish late-injecting
extensions.

---

## Why MCC, and why random `k`

With **fixed** k=5 and a published pool, precision ≡ recall ≡ F1 mechanically — every false
positive is matched by a false negative. Random guessing floors at `5·(5/N)` ≈ 25% for N=20.

Randomizing `k` makes cardinality part of the prediction. MCC over all N decisions scores ~0 for
every trivial strategy (all-true, all-false, random).

```
MCC = (TP·TN − FP·FN) / sqrt((TP+FP)(TP+FN)(TN+FP)(TN+FN))
      → 0.0 when the denominator is 0 (miner answered all-one-class)
score_trial = max(0.0, MCC)
score_run   = mean over trials
```

This matters because of what sits downstream: the validator feeds the `[0,1]` score into a
**temperature softmax** (`_apply_softmax`, temp 0.2 template / 0.05 flowprint) plus a `min_score`
gate. Small absolute gaps get amplified hard, so the metric must **spread** miners rather than
saturate. MCC does; F1-over-fixed-5 does not.

---

## Why scoring lives here and not in `stack-redteam`

`stack-redteam` runs `rest-scoring-api` ("centralized validator") with `/var/run/docker.sock`
mounted and `network_mode: host`. It is the **orchestrator**, and there are two distinct things
called "scoring":

| Layer                               | Question                                                                                                                         | Where           |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | --------------- |
| Challenge container (**this repo**) | "Did this JS correctly identify which extensions were enabled?" → one float in `[0,1]`                                           | `service.py`    |
| `scoring-api` / `redteam_core`      | "What is that float worth in TAO across all miners?" → similarity penalty, time decay, softmax, sybil collapse, on-chain weights | `stack-redteam` |

`scoring-api` cannot do the first. It has no concept of a browser extension, and critically **it
does not know the ground truth** — our container picks the random subset and drives the browser,
so only we know which 3–8 were enabled. The validator receives a single number.

This is also why `controller.py` / `challenge_manager.py` are out of scope: they are the plugins
`scoring-api` loads for the _second_ layer.

**Consequence for the container spec:** in production `scoring-api` launches our image itself via
the Docker socket, using `challenge_container_run_kwargs` from `active_challenges.yaml`. Our
`compose.yml` is **local dev only and is ignored in prod**. Every hardening setting — `shm_size`,
`tmpfs`, `mem_limit`, `pids_limit`, `cap_drop`, seccomp, the internal network — must be written
in _both_ places, or it silently won't apply where it matters. Ship the required
`challenge_container_run_kwargs` block in `docs/design.md` as part of the registration handoff.

## Architecture

```
validator ──GET  /task ─────────────► challenge API (:10001)
          ──POST /score ────────────►   │  {miner_input, miner_output} → bare float
                                        │
                                        ▼
                          for trial in 1..T:
                            pick random k∈[3,8], random subset of pool
                            launch Chrome for Testing (headless)
                              --load-extension=<k unpacked dirs>
                            navigate to bait page (served locally)
                            settle N seconds
                            execute_async_script(miner JS, budget)  → {extId: bool}
                            quit driver
                          score = mean(max(0, MCC_trial))
```

The bait page is served by the challenge API itself and is **completely static** — no ground
truth in the DOM, no query params, no globals.

---

## Project structure

Only the files we add or change. Everything else is inherited scaffold.

```
extension-classification/
├── extensions.yml                      NEW  the pool: ~30 store IDs + version + signal note
├── compose.yml                         EDIT shm_size, seccomp, cap_drop, tmpfs, mem_limit
├── docs/
│   ├── README.md                       DONE index
│   ├── design.md                       DONE this document
│   └── architecture.excalidraw         DONE flow diagram
├── scripts/
│   └── fetch_extensions.py             NEW  build-time: download CRX, unpack, inject key
├── examples/miner_commit/
│   └── src/commit/solution.js          EDIT reference baseline detector
└── src/bex_tracer/
    │   # controller.py / challenge_manager.py deliberately untouched — see below
    └── challenge/
        ├── Dockerfile                  EDIT + Chrome for Testing + chromedriver + extensions
        ├── requirements.txt            EDIT + selenium, psutil
        ├── templates/index.html        NEW  bait page
        └── api/
            ├── core/configs/_challenge.py   NEW  pool path, T, k range, P, timeouts, api_key
            └── endpoints/challenge/
                ├── router.py           EDIT add auth to /score
                ├── schemas.py          EDIT solution.js contract + pool in MinerInput
                ├── service.py          EDIT orchestration only, replacing random.random()
                ├── _payload_manager.py NEW  trial schedule (ground truth) + predictions + MCC
                └── _browser.py         NEW  launch Chrome, run one trial
```

**Six new files.** Note on `_payload_manager.py`: the cookiecutter template ships **no** payload
manager — `endpoints/challenge/` contains only `__init__.py`, `router.py`, `schemas.py` and the
`random.random()` stub in `service.py`, and `grep -ril payload_manager` over the template returns
nothing. ADA3 and flowprint each added theirs by hand. It's a convention the real challenges
converged on, not inherited scaffolding, so we add ours too:

| ADA3                              | Ours                  | Role                                                                  |
| --------------------------------- | --------------------- | --------------------------------------------------------------------- |
| `service.py` (251 lines)          | `service.py`          | Orchestration. **Never computes a score** — calls `calculate_score()` |
| `_payload_manager.py` (300 lines) | `_payload_manager.py` | Ground truth + predictions + metric + scoring constants               |
| `_bot_runner.py`                  | `_browser.py`         | How we drive a browser, kept out of the HTTP layer                    |

`_payload_manager.py` owns, exactly as ADA3's does:

- `build_trial_schedule(pool, T, k_range)` → per-trial enabled subsets. This **is** the ground
  truth, and keeping it separable makes it unit-testable on its own (ADA3 has a dedicated
  `tests/test_run_schedule.py` for precisely this).
- the recorded per-trial predictions
- `calculate_score()` → `mean(max(0, MCC))`, plus scoring constants at module top

Deliberately _not_ creating:

- **`_crx.py`** — CRX3 parsing is build-time only, called from `fetch_extensions.py` and never at
  runtime. It has no business in the API package; it lives inside that script.
- **`_scoring.py`** — would be redundant with `_payload_manager.py`, which is where both ADA3 and
  flowprint keep their metric.

`fetch_extensions.py` is Python rather than the shell one-liner I'd planned, because the
key-injection step needs CRX3 header parsing.

---

## Extension pool

The pool is a **hand-curated config file**, not a tool. Picking it is a one-time human job:
choose ~30 popular extensions with known page-visible fingerprints, confirm each shows a signal
in a headless throwaway session, write down what the signal is, move on.

**`extensions.yml`** — single source of truth, committed:

```yaml
pool:
    - id: cjpalhdlnbpafiamejdnhcphjbkeiagm
      name: uBlock Origin
      version: "1.x.y"
      sha256: "..." # of the .crx; a mismatch should fail the build loudly
      signal: web_accessible_resources
      use_dynamic_url: false # true => WAR probing is dead for this one
      time_to_stable_ms: 1200 # feeds the settle window
```

Rules when picking, all checked in the same pass:

- **Verify headless, with egress already blocked** — that's what prod looks like. An extension
  that only reveals itself with a visible window, or only with internet access, is a trap.
  Popup UI, context menus, notifications and `action.onClicked` never fire headless;
  content-script injection, DNR blocking and `web_accessible_resources` all work fine.
- **Check `use_dynamic_url` explicitly, per extension.** If a `web_accessible_resources` entry
  sets it, Chrome randomizes that extension's resource URLs per session and WAR probing against
  it is dead — the single richest technique, gone, for that extension. Adoption has been climbing
  through the MV3 migration, so on a pool of ~30 popular extensions this could plausibly knock
  out more than a handful. Grep every candidate's manifest during curation and record the result
  in `extensions.yml`. If it removes WAR probing from a large fraction of the pool, that's a
  finding that changes pool composition, not a footnote.
- **Record time-to-stable-footprint** (drives the settle window — see below).
- **Skip anything invisible to a page** (pure background/service-worker extensions).
- **Reject anything nondeterministic.** If an extension's footprint varies across repeat runs,
  it's permanent label noise that caps achievable MCC below 1.0 for reasons no miner can beat.
- **Go easy on heavy ad blockers.** Static DNR rulesets are re-indexed on _every_ launch and
  can't be cached (Chrome wipes `_metadata/` each load). A uBlock-class extension costs 1–4s per
  trial. One or two per subset is fine; fifteen would make scoring unusably slow.

### `scripts/fetch_extensions.py` — build time only

Download per pinned ID, verify sha256, unpack, **inject the `key` field**:

```
https://clients2.google.com/service/update2/crx?response=redirect
  &acceptformat=crx2,crx3&prodversion={CFT_VERSION}&x=id%3D{ID}%26uc
```

**The key-injection step is mandatory and was a correction to the earlier draft.** Store `.crx`
manifests contain **no** `key` field — the store rejects uploads that have one, and Chrome
injects it at install time via `SandboxedUnpacker`, which loading unpacked bypasses. Without it,
Chrome derives the ID from the **directory path**, so `chrome-extension://<real-id>/` probes all
fail and every piece of public prior-art is useless against our pool.

So: parse the CRX3 header, pull the publisher's public key, write it base64 into
`manifest.json` as `key`. One gotcha — CRX3 headers carry **several** `AsymmetricKeyProof`
entries (a live sample had 3). Taking the first gives the wrong ID silently. Match each
candidate key against `signed_header_data.crx_id` (field 10000) and use the one that hashes to
it. ID derivation is `SHA256(spki)[:16]` with nibbles mapped `0-f → a-p`.

Also in this script: strip `update_url`, drop `_metadata/verified_contents.json`, read manifests
as `utf-8-sig` (real store manifests ship BOMs), and assert the derived ID equals the pinned one.
Binaries are never committed.

---

## Work breakdown

### 1. Land the docs in the repo — ✅ done

`docs/design.md` (this file), `docs/architecture.excalidraw` (flow diagram, openable at
excalidraw.com) and `docs/README.md` (index) are in place.

Note there is still no `mkdocs.yml`, despite `MANIFEST.in` and `.vscode/` referencing one.
Keep these docs updated as phases land — they double as the miner-facing spec, and the published
extension pool has to live somewhere miners can read.

### 2. Container — Chrome for Testing

`src/bex_tracer/challenge/Dockerfile`, extending the existing 3-stage build:

- Pin **Chrome for Testing** + matching chromedriver by version _and_ sha256, from
  `https://storage.googleapis.com/chrome-for-testing-public/{VERSION}/{PLATFORM}/{BINARY}-{PLATFORM}.zip`.
  Discover versions via the CfT JSON endpoints; never resolve "Stable" at build time.
  **`selenium/standalone-chrome` is unusable** — it ships google-chrome-stable, and Chrome 137
  removed `--load-extension` with Chrome 142 removing the workaround.
  CfT is safe long-term: the block is a compile-time `GOOGLE_CHROME_BRANDING` buildflag, not a
  runtime feature, so it can't be flipped on us without a source change.
- **Use the full `chrome` binary. Never `chrome-headless-shell`** — it has no extensions layer,
  so extensions simply don't exist there. Hard blocker, not a degradation.
- **Debian trixie package names.** Trixie finished the 64-bit `time_t` transition, so it's
  `libasound2t64`, `libatk1.0-0t64`, `libatk-bridge2.0-0t64`, `libatspi2.0-0t64`, `libcups2t64`,
  `libglib2.0-0t64`. Pasting a bookworm dependency list here is the most likely way this build
  fails.
- **`tini` as PID 1** (or `init: true`). Chrome forks a zygote plus renderers; without a reaper
  we accumulate zombies across trials.
- Xvfb installed but **only started behind a dev flag**. Running it in prod "just in case"
  changes Chrome's code path and invalidates headless-vs-headful equivalence.
- Add `selenium` and `psutil` to `challenge/requirements.txt`. Pass `executable_path` explicitly
  so Selenium Manager never runs — it phones home.

**Platform note:** CfT ships no `linux-arm64` before v153. On Apple Silicon, either run
`--platform=linux/amd64` (slow under emulation) or pin a 153+ build. Worth deciding early —
it affects local dev ergonomics.

**Build-time gates** — fail the image, not the first `/score`: `ldd chrome` has no "not found";
`chrome --version` matches the pin; a one-extension headless launch actually loads it (this is
the canary for a future CfT change); every unpacked manifest's derived ID equals its pinned
store ID; no extension path contains a comma.

`compose.yml` (**dev**) _and_ the `challenge_container_run_kwargs` block for
`active_challenges.yaml` (**prod**) — both need: `shm_size: 2gb` (the 64MB default crashes tabs),
`tmpfs` for trial scratch, `mem_limit: 8g`, `pids_limit`, `cap_drop: ALL`, `no-new-privileges`,
seccomp profile, and an `internal: true` network (see Network policy). Prod ignores `compose.yml`
entirely, so keep the two in sync and document the yaml block in `docs/design.md`.
**Do not use `privileged: true`** even though sibling challenges do — we execute miner-submitted
JavaScript here, and privileged disables seccomp, AppArmor and all capability drops. Arbitrary
attacker JS plus a privileged container is a renderer-RCE-to-host-escape chain.

### 3. Browser runner

**`api/endpoints/challenge/_browser.py`** — one job: `run_trial(subset, miner_js) -> dict[str, bool]`.
Launch, navigate, settle, inject, collect, quit.

Care points:

- **Copy the k selected extension dirs into per-trial scratch before launching.** Chrome calls
  `MaybeCleanupMetadataFolder()` on every unpacked load, deleting and rebuilding
  `<ext>/_metadata/generated_indexed_rulesets/`. If the extension dir isn't writable, static DNR
  rules **silently fail to apply** — ad blockers become no-ops, labels are wrong, and no error is
  raised. This is the most dangerous failure mode in the design. Copying also avoids two
  Chrome mutating the source `_metadata` dir. Because we injected `key`, the path change
  doesn't change the ID — which is exactly why key injection is non-negotiable.
- `--headless=new` in prod. (Old headless was removed in Chrome 132; the flag is now a synonym,
  but pass it to document intent.)
- **Don't blanket-copy a "disable everything" flag list.** `--disable-extensions` loads then
  disables our extensions — only `--disable-extensions-except` grants an exemption, and
  `--load-extension` doesn't get one. `--incognito`, `--guest`, `--single-process` and
  `--disable-component-extensions-with-background-pages` all break things too. Keep the list
  minimal.
- **Assert the extensions actually loaded**, by ID, right after launch. A mis-keyed extension
  otherwise becomes a permanent silent false negative in every trial it appears in.
- Fresh `--user-data-dir` per trial. No seed-profile snapshotting — it would need warming via
  `--load-extension` specifically (profiles warmed any other way make `InstalledLoader` resurrect
  all 30 extensions on every launch, silently destroying subset selection).
- `driver.quit()` in a `finally`, plus a process-group sweeper — `quit()` fails exactly when
  cleanup matters most (hung renderer, script timeout). Filter the sweeper by the trial's
  scratch path so stale processes cannot affect a later trial.
- `set_script_timeout` must be strictly greater than the in-script timeout, or Selenium raises
  before our own sentinel fires and we lose the diagnostic.

### 4. Bait page

`challenge/templates/index.html` + static assets, served at `/_web`.

A blank page gives extensions nothing to react to. Include: a `<input type="password">` in a real
`<form>` (password managers), ad-shaped divs — `#ad-banner`, `.adsbygoogle`, 728×90 / 300×250 —
(ad blockers), a textarea (grammar tools), images, external links, a cookie banner, light
backgrounds (Dark Reader recoloring), a `<canvas>`.

Constraint: **zero ground truth**. Generate script tags from config rather than hardcoding them —
hardcoded tags silently drifting from config is a real bug in ADA3's `index.html`.

### 5. Scoring + API

- **`_payload_manager.py`** — `build_trial_schedule()` (ground truth, `secrets`-backed),
  per-trial prediction recording, and `calculate_score()` → `mean(max(0, MCC))`. MCC must handle
  the all-one-class → 0.0 edge case.
- **`service.py`** — replace the `random.random()` stub with **orchestration only**: build the
  schedule, loop trials calling `_browser.run_trial()`, record each result, then return
  `calculate_score()`. It should not contain the metric, matching ADA3. Keep `score` a
  **sync `def`**: FastAPI runs it on the threadpool, which is what keeps the API responsive
  during a multi-minute run. Making it `async def` deadlocks the design.
- **`schemas.py`** — require exactly one file, `solution.js`, ≤500 lines (reuse the existing
  validator). `MinerInput` carries the published pool so miners know the closed world.
- **`router.py`** — add `Depends(auth_api_key)` on `/score`; the template ships it unguarded.
- **`core/configs/_challenge.py`** — new, following flowprint's: pool path, trial count, k range,
  settle seconds, script budget, api_key.
- Single-flight guard on `/score` (flowprint's `ScoringStatus` pattern) so concurrent calls can't
  interleave.

### 6. Miner-facing

- Contract: `solution.js` defines `window.detect_extensions = async function() { ... }` returning
  `{extensionId: boolean}`. Missing keys → `false`.
- **`examples/miner_commit/src/commit/solution.js`** — reference baseline that really detects 2–3
  easy extensions via WAR probing, so miners have a working start.

### 7. Scaffold bugs (small, real)

- `myhub/rest-EXC-commit` is an **invalid Docker reference** (uppercase) — in
  `examples/miner_commit/compose.yml:3`, `scripts/build.sh:31`, its README, and
  `templates/compose/compose.override.dev.yml:27`. Rename to
  `redteamsubnet61/submission-exc-challenge`.
- `.vscode/settings.json:155` — un-rendered `src/my_challenge/challenge` path.
- `pyproject.toml:65-69` — `[project.urls]` still point at `challenge-template`; `"template"`
  keyword at line 23.
- `volumes/configs/rest-exc-challenge/` is empty, so the container runs on bare pydantic defaults
  — the `templates/configs/challenge/*.yml` are never installed there.

---

## Not in scope: `controller.py` / `challenge_manager.py`

**Leave both untouched.** They are validator-side plugins, loaded only via the `target:` and
`challenge_manager:` dotted paths in the RedTeam repo's `active_challenges.yaml`. Until this
challenge is registered there, they are dead code and nothing we build imports them.

For the record, what's in them and when it will matter:

| Item                                                                               | Status                                                                                                                                                                                             |
| ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `_score_miner_with_new_inputs` loops over inputs but only writes `scoring_logs[0]` | **Latent, never fires for us.** `num_tasks` defaults to `N_CHALLENGES_PER_EPOCH = 1`, so there's exactly one input. We run T trials inside a single `/score`, so we want `num_tasks: 1` regardless |
| `_exclude_output_keys` is a no-op (`return`)                                       | Only affects the anti-plagiarism comparison payload. ADA3 and flowprint null `commit_files`/`telemetry`/`scoring_results`. Decide at registration                                                  |
| Class names `MyController` / `MyChallengeManager`                                  | Cosmetic. The yaml points at whatever path we write                                                                                                                                                |
| Not exported from `src/bex_tracer/__init__.py`                                     | Only needed to re-enable `tests/test_module.py`, which is 100% commented out                                                                                                                       |
| `min_score` / `reward_temperature` in the manager                                  | **Can't be chosen yet.** Needs the real MCC distribution from working baselines. Genuinely a later decision                                                                                        |
| `commit_timestamp + 1 + 24 + 60 + 60` (meant to be `1*24*60*60`)                   | Inherited template bug, upstream's problem, doesn't affect scoring correctness here                                                                                                                |

Revisit as a single pass when registering the challenge.

---

## Anti-cheat

| Vector                             | Mitigation                                                                                                                                           |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ground truth in the page           | Bait page fully static; subset chosen server-side, never serialized anywhere the browser can reach                                                   |
| Cross-trial state                  | Fresh `--user-data-dir` per trial                                                                                                                    |
| Forged results                     | Results come back through `execute_async_script`. **Do not** copy ADA3's `/_payload` — unauthenticated and accepts an attacker-chosen `order_number` |
| Always-guess strategies            | MCC scores them ~0 by construction                                                                                                                   |
| Copying other miners               | Existing `comparison_config` / similarity pipeline in `redteam_core`                                                                                 |
| Crashing the browser               | Per-trial try/except → that trial scores 0, run continues                                                                                            |
| Miner JS as an SSRF / abuse vector | Deny-by-default egress on an `internal: true` network — see below                                                                                    |

### Network policy — deny-by-default egress at runtime

This is a **build requirement, not a follow-up.** The container runs arbitrary miner-supplied
JavaScript; unrestricted egress makes it a live SSRF and abuse vector against anything reachable
from its network namespace. That's the real exposure — data exfiltration is the lesser half, and
framing it as "little useful to exfiltrate given randomized subsets" undersold it.

The good news is this is now cheap, because there is no runtime reason to reach the internet:

- **CRX downloads are build-time only.** `clients2.google.com` is needed in the build stage and
  never again. Nothing at scoring time fetches from Google.
- **Seed profiles are cut**, so there's no warm-up phase that needs the network.
- **MV3 ad blockers ship their static DNR rulesets inside the extension.** Rule-based blocking —
  our main behavioral signal — works fully offline. (My earlier reasoning that egress-blocking
  "breaks ad blockers fetching filter lists" was wrong for MV3.)

So: run trials on an `internal: true` Docker network, reachable only to the locally-served bait
page. Flowprint already does exactly this for its sandbox container — house pattern exists.

Caveat to fold into pool curation: some extensions degrade or go silent without network.
**Curate with egress already blocked**, since that's what prod looks like. An extension whose
signal only appears with internet access doesn't belong in the pool.

---

## Risks

1. **Web Store terms.** Downloading `.crx` from `clients2.google.com` sits outside CWS
   distribution terms — accepted per decision. Mitigated by downloading at build time and never
   committing binaries. Fallback if it becomes a problem: open-source extensions from GitHub
   releases with a `key` field injected into `manifest.json` to preserve real store IDs.
2. **Headless signal loss.** Prod is headless; some extensions inject differently or not at all
   without a visible window. Hence the verify-headless rule when picking the pool.
3. **`use_dynamic_url` shrinking the viable pool.** Extensions setting it randomize their
   resource URLs per session, killing WAR probing for that extension. Adoption is rising with the
   MV3 migration, so this could take out a meaningful slice of ~30 popular extensions rather than
   one or two. Now a hard per-extension check during curation, recorded in `extensions.yml`. If
   the count comes back high, pool composition needs rethinking before we build the harness.
4. **Version drift.** Pinned versions go stale and a silent update can move a fingerprint. Re-check
   the pool periodically.
5. **Throughput.** See the concurrency budget below — decided up front, not deferred.
6. **Flaky ground truth.** Some signals appear seconds after `load` and inconsistently. Settle
   window plus averaging over T trials is the defense — see the settle-tuning method below.
7. **Difficulty spread.** If most of the pool is trivially detectable via WAR probing, everyone
   saturates and ranking becomes noise. Watch this once baselines exist.

---

## Concurrency budget

Pinned now, because if `T` has to rise later for score stability, sequential execution eats the
whole time budget and we'd be re-architecting under pressure.

|                     |                                                                  |
| ------------------- | ---------------------------------------------------------------- |
| Per-trial wall time | 4–9s (k=5, one heavy ad blocker; DNR re-indexing dominates)      |
| Peak RAM per Chrome | ~0.7–1.2 GB (peak is the DNR flatbuffer build, not steady state) |
| Round execution     | Sequential — one browser at a time                               |
| `mem_limit`         | 8 GB                                                               |
| `shm_size`          | 2 GB                                                               |
| Total run time      | Sum of per-round wall times                                      |

So P=4 buys roughly a 4× headroom on `T` before we're anywhere near the validator's tolerance.
`P` and `T` both configurable; `P` is bounded by RAM, not CPU.

Two guardrails:

- **OOM canary.** Watch the cgroup `memory.events` for `oom_kill` and fail the score request
  loudly. An OOM-killed trial otherwise returns garbage labels that look like a bad miner.
- **Scratch leak.** Trial dirs live on tmpfs; a crash before cleanup leaks. Sweep `exc-trial-*`
  older than 10 minutes at `/score` entry.

Emit per-phase timings in the response debug payload from day one — the numbers above are
estimates and there's no published measurement for this workload shape.

## Settle window — how to pick it

`settle_seconds` is not a feel-based constant. Derive it, then verify it:

1. **During pool curation**, for each extension record its _time-to-stable-footprint_: poll the
   page footprint (injected nodes, stylesheets, blocked requests) every 250ms and note when it
   stops changing. Store it in `extensions.yml` alongside `signal`.
2. **Set `settle_seconds` = p99 of those measurements**, floored at 2s and capped at 8s. An
   extension needing more than 8s is too slow and too flaky — drop it from the pool rather than
   paying its latency on every trial.
3. **Verify against the determinism check** (verification step 5): score the same submission 5×
   and require low MCC variance. High variance means the settle is too short — raise it and
   re-run, don't compensate by bumping `T`.
4. **Prefer a readiness predicate over a fixed sleep** where possible: wait for footprint
   stability with `settle_seconds` as the hard cap. Cheap trials then finish early, which
   directly buys back throughput.

---

## Verification

1. **Extension loading smoke test** — the highest-risk assumption, do it first. Launch CfT
   headless with 5 unpacked extensions and assert all 5 are enabled **under their real store
   IDs**. Nothing else is worth building until this passes.
2. **DNR sanity check** — with an ad blocker in the subset, confirm an ad-shaped request on the
   bait page is actually blocked. This is the check that catches the silent read-only
   `_metadata` failure; without it a broken build looks identical to a working one.
3. **`_payload_manager.py` unit tests** — MCC against known confusion matrices plus the
   all-one-class → 0.0 edge case, and `build_trial_schedule()` for k-range bounds and subset
   distribution (the analogue of ADA3's `tests/test_run_schedule.py`). A random-guess simulation
   over the real pool should average ~0.
4. **Baseline miner** — the reference `solution.js` scores clearly above 0 and clearly below 1.
   Stubs returning all-`false`, all-`true`, and random must each score ≈0.
5. **Determinism** — score the same submission 5×; MCC variance should be small. High variance
   means the settle window needs work.
6. **End-to-end** — `./compose.sh start -l`, then `curl localhost:10001/health`,
   `curl localhost:10001/task`, and `POST /score` with `{"miner_input": ..., "miner_output": ...}`
   plus `X-API-Key`, asserting a bare float in `[0,1]`.
7. **Miner container** — build `examples/miner_commit`, `POST /solve` on :10002, feed its output
   straight into `/score`.
8. **Existing tests** — `tests/test_challenge_api.py` and `test_miner_commit_api.py` stay green.
   `tests/test_module.py` is fully commented out and stays that way — it tests the controller,
   which is out of scope.
