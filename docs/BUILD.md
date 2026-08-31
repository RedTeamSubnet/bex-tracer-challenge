# Build Sheet — Extension Classification Challenge

Execution checklist. The **why** lives in [`design.md`](./design.md); this is the **how**.
Work top to bottom. Do not skip Step 0.

Every step has an **Acceptance** block. If it doesn't pass, stop and fix it — do not continue.

---

## 📋 What is left — start here

Snapshot 2026-08-29. Steps 0, 2, 3, 5, 6 and 8 are done and verified. What follows is
everything still open, in the order it unblocks the rest. Each item states its own acceptance
so it can be closed without re-reading the step it came from.

### Status at a glance

| Step | State | Blocking? |
|---|---|---|
| 0 · assumptions | ✅ done | — |
| 1 · extension pool | ⛔ **open** — signals predicted from manifest shape, never measured | **yes** — gates the metric |
| 2 · `fetch_extensions.py` | ✅ done | — |
| 3 · `_browser.py` | ✅ done | — |
| 4 · bait page | 🟡 written, **acceptance not met** | yes — a broken build looks healthy |
| 5 · scoring + API | ✅ done | — |
| 6 · container | ✅ done — image builds, gates pass | — |
| 7 · miner baseline | 🟡 `solution.js` written (142 lines), **never scored end-to-end** | yes |
| Final verification | ⛔ not run | yes |

---

### A. Rebuild before anything else

The published image `redteamsubnet61/rest-exc-challenge:0.0.0` **predates the 2026-08-29 fixes**
and still contains the `/score` crash described in item C1. Rebuild first; every measurement
below is void otherwise.

**Acceptance:** image builds, all five `verify_chrome_build.py` gates pass.

---

### B. Step 1 — curate the pool against real Chrome  👤 human judgment

The long pole, and the only item that cannot be automated end to end.

`time_to_stable_ms` is `null` for **all 29 entries** in `extensions.yml`. Every "this extension
is detectable" claim in this document is currently inferred from manifest shape, not observed.

- Run `run_round.py` per extension, headless, egress blocked, **5 repeat runs** each.
- Record `time_to_stable_ms` and whether the signal appears at all.
- 19 have static WAR paths and should pass on the fetch probe.
- 8 have no usable WAR and need a DOM/CSS/DNR signal; 4 of those are expected to fail.

**Acceptance:** ≥20 extensions with signals stable across 5 runs. Landing at 23 is fine and
needs no re-sourcing.

**Then:** set `settle_seconds` to the **p99 of the measured `time_to_stable_ms`**, floor 2s,
cap 8s. The shipped `4.0` is a guess with nothing behind it.

---

### C. Correctness gaps found during the 2026-08-29 review

**C1 — fixed, needs no action, recorded so it is not reintroduced.**
`BaseConfig` sets `extra="allow"` with `env_file=".env"`, so every config subclass absorbs every
key in `.env` — including the API key. Four `**config.x.model_dump()` splat sites therefore
received unexpected kwargs; `service.py` raised `TypeError` on **every `/score` call**. Only the
dotenv file source leaks, not real env vars, so the container was unaffected and local dev was
totally broken. Fixed by `BaseConfig.declared_dump()`. **Never splat a bare `model_dump()` from
this config tree.**

**C2 — `max_parallel_rounds` is dead config.** Declared in `_challenge.py` and both
`challenge.yml` files, read by no Python code; `service.py` runs rounds sequentially. At the
defaults a `/score` call is roughly 20 × (settle + script budget + Chrome launch) ≈ **7 minutes**.
Decide from the measured time in item D: implement the parallelism, or delete the field. Leaving
it as-is means the config lies about what the service does.

**C3 — fixed 2026-08-30.** `interact()` now runs on every scored round.
`run_round()` does `launch -> open_page -> interact -> run_script`, with a short
`_GESTURE_SETTLE_SEC` pause so an overlay injected by the gesture is present before the DOM is
sampled. Without it the whole password-manager class of the pool was undetectable: 5 of 27
entries are password managers (Bitwarden, LastPass, 1Password, Dashlane, NordPass), and
Bitwarden has no usable WAR, so a gesture may be its only signal. The gesture script is fixed
and identical every round, so it leaks nothing. `_GESTURE_SETTLE_SEC = 1.5` is provisional and
should be measured alongside `time_to_stable_ms` during item B.

**C4 — Step 4 acceptance is still unwritten.** No assertion that an ad-shaped element is
measurably blocked when an ad blocker is in the subset. This is also the check that catches the
read-only `_metadata` trap, so until it exists a broken build is indistinguishable from a
working one.

---

### D. Final verification — none of it has been run

1. **End-to-end.** `scripts/run_local_challenge.py` — `GET /health`, `GET /task`, `POST /score`
   with `X-API-Key`, asserting a bare float in `[0, 1]`. **Time this run**; the number decides C2.
2. **Step 7 acceptance.** Feed the miner container's `/solve` output straight into `/score` and
   get a score clearly above 0 and clearly below 1.
3. **Determinism.** Score the same submission 5×. If MCC variance is high, **raise the settle
   window — do not bump `T`**.

---

### E. Cleanup

**Done 2026-08-30:**

- `scripts/validate_selenium.py` deleted (258 lines, unreachable - never copied into the image).
- `smoke_chrome.py`, `dev_round.py`, `enable_extensions.py` and `_harness.py` merged into
  **`src/exc_challenge/challenge/scripts/run_round.py`** (568 -> 396 lines, 4 files -> 1). It
  detects container vs checkout layout, so the same script runs in both. `--rounds 0` replaces
  `enable_extensions.py`; `--miner <path>` replaces `dev_round.py`; the default WAR-probe miner
  replaces `smoke_chrome.py`. This also removed the duplicated `_ALL_URLS` and bait-server code.
- The inert `# noqa` comments (ruff codes in a repo with no ruff) are gone; the reasons attached
  to them were kept as plain comments.

**Still open:**

- **Pool readers, 3 copies**: `_pool.load_pool_ids`, plus inline YAML in `screen_extensions.py`,
  `verify_chrome_build.py` and `fetch_extensions.py`. The last two are bind-mounted alone into
  separate build stages and cannot import a shared module without widening the Dockerfile mounts.
- **Two CRX-ID implementations**: `fetch_extensions.encode_crx_id` and
  `verify_chrome_build.derive_id`. Forced apart by the same build-stage isolation - keep in sync
  by hand.
- **`_ALL_URLS` + WAR parsing** still exists in both `run_round.py` and `screen_extensions.py`.
  The latter is network-only curation that never enters the container, so sharing would mean
  shipping code the image does not need.
- **`os.chmod(dst, 0o755)`** in `_browser.py` - `0o700` is tighter and still satisfies the
  owner-write requirement, but was not changed without a real Chrome run to confirm.
- **`auth.py` rejects keys of exactly 8 characters** (`len(api_key) <= 8`) while the documented
  range is 8-128. Errs strict, so harmless.

---

## ⚠️ Traps — read before writing any code

At each of these, the obvious choice is the wrong one. These are the most likely ways this
build silently produces garbage instead of failing loudly.

| Trap | Wrong | Right |
|---|---|---|
| Loading extensions | `options.add_extension("x.crx")` — ChromeDriver unpacks to a temp dir **it** owns; we lose path control | `options.add_argument("--load-extension=dir1,dir2")` with dirs we unpack |
| BiDi install | `options.enable_webextensions = True` — forces `--remote-debugging-pipe`, **disables most CDP**, installs post-startup (race) | Don't use it |
| Chrome flags | Pasting a standard "disable everything" list. `--disable-extensions` **loads then disables** our extensions | Minimal flag list, and assert extensions loaded |
| Extension dirs | Mounting `/opt/extensions` read-only. Chrome rewrites `_metadata/` every load; if it can't, **static DNR silently no-ops** and labels are wrong with no error | Copy the k dirs into per-round writable scratch |
| Extension IDs | Assuming the store `.crx` keeps its real ID. It does **not** — the manifest has no `key`, so Chrome derives the ID from the **directory path** | Inject `key` from the CRX3 header at build time |
| CRX3 parsing | Taking the first public key in the header. Headers carry several; a live sample had 3 | Match against `signed_header_data.crx_id` (field 10000) |
| Chrome binary | `chrome-headless-shell` — has **no extensions layer at all** | The full `chrome` binary with `--headless=new` |
| Base image | `selenium/standalone-chrome` — ships google-chrome-stable, which **removed `--load-extension`** in 137 | Chrome for Testing, pinned |
| `score()` | `async def score(...)` — **deadlocks**; a run takes minutes | `def score(...)`; FastAPI puts sync handlers on the threadpool |
| Container | `privileged: true` (siblings use it) — we run untrusted miner JS | `cap_drop: ALL` + seccomp + `no-new-privileges` |

---

## Pinned constants

```
CFT_VERSION   = 152.0.7977.54     # Chrome for Testing, Stable as of 2026-08-24
CFT_PLATFORM  = linux64           # prod. mac-arm64 for local experiments (Step 0)
K_RANGE       = [3, 8]            # extensions enabled per round
T_ROUNDS      = 20                # configurable
P_PARALLEL    = 4                 # configurable, RAM-bound
```

Version discovery (do **not** resolve "Stable" at build time — pin it):
```sh
curl -s https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json
```
Download URL pattern:
```
https://storage.googleapis.com/chrome-for-testing-public/{VERSION}/{PLATFORM}/{BINARY}-{PLATFORM}.zip
# BINARY in {chrome, chromedriver}
```

---

## Step 0 — Prove the core assumptions ✋ STOP GATE

**Nothing else is worth building until this passes.** Runs natively on macOS (`mac-arm64`) or
Linux (`linux64`). No Docker. Work in a scratch dir outside the repo.

Three assumptions, in order of how badly they'd hurt:

1. A store `.crx`, unpacked with an injected `key`, loads under its **real store ID**
2. Chrome for Testing still honours `--load-extension`
3. A page-context `fetch("chrome-extension://<id>/...")` actually detects it

Procedure:

1. Download CfT `chrome` + `chromedriver` for your platform, unzip, `chmod +x`.
2. Download one `.crx`:
   ```
   https://clients2.google.com/service/update2/crx?response=redirect&acceptformat=crx2,crx3&prodversion=152.0.7977.54&x=id%3D<ID>%26uc
   ```
3. Parse the CRX3 header (see Step 2 for the algorithm), extract the matching public key,
   assert `derived_id == <ID>`.
4. Unzip the payload, write `key` (base64 SPKI) into `manifest.json`, strip `update_url`,
   delete `_metadata/verified_contents.json`. Read the manifest as **`utf-8-sig`** — real store
   manifests ship BOMs.
5. Launch headless with `--load-extension=<dir>` and a fresh `--user-data-dir`.
6. Enumerate loaded extensions and assert the real store ID is present.
7. From page JS, `fetch()` a `web_accessible_resources` path under that ID and check it resolves.

**Acceptance:** all three assumptions hold on at least one real extension.

**If step 3 or 6 fails** → key injection is wrong; the whole CRX approach needs rethinking.
**If step 7 fails** → check whether that extension sets `use_dynamic_url`; try another before
concluding the premise is broken.

---

## Step 1 — Extension pool  👤 human judgment required

**This step is curation, not coding.** A model can run the probes; a person decides what stays.

Produce `extensions.yml` at the repo root:

```yaml
pool:
  - id: cjpalhdlnbpafiamejdnhcphjbkeiagm
    name: uBlock Origin
    version: "1.x.y"
    sha256: "..."              # of the .crx
    signal: web_accessible_resources
    use_dynamic_url: false
    time_to_stable_ms: 1200
```

Starter candidates — **verify each ID against its Chrome Web Store page before use**, and have
the fetch script print the manifest `name` so a wrong ID is caught by eye:

| Kind | Examples |
|---|---|
| Ad / content blockers | uBlock Origin, uBlock Origin Lite, AdBlock, Adblock Plus, Ghostery |
| Password managers | Bitwarden, LastPass, 1Password |
| Appearance | Dark Reader, Stylus |
| Privacy | Privacy Badger, ClearURLs, Decentraleyes |
| Consent | I still don't care about cookies, Consent-O-Matic |
| Userscript | Tampermonkey, Violentmonkey |
| Wallets | MetaMask |
| Utility | SponsorBlock, Wappalyzer, JSON Formatter, Refined GitHub, Vimium |

Accept an extension only if **all** hold:
- Shows a page-visible signal **headless, with egress blocked** (that's prod)
- Signal is **stable across 5 repeat runs** — flaky extensions are permanent label noise that
  cap achievable MCC for reasons no miner can overcome
- `use_dynamic_url` recorded (if `true`, WAR probing is dead for it — note it, don't auto-reject)
- `time_to_stable_ms` recorded

Keep heavy DNR extensions (uBlock-class) to a small fraction — each costs 1–4s per round in
ruleset re-indexing that cannot be cached.

**Acceptance:** ≥20 extensions with stable headless signals, spread across difficulty. If
`use_dynamic_url` knocks out a large share, stop and reconsider pool composition before Step 3.

### 📋 Pool status — updated 2026-08-27

**Pool replaced with the maintainer's 28-candidate list.** Every id was verified against the
live Chrome Web Store: the .crx was downloaded and the id **derived from the CRX3 signing key**,
so a copied-from-the-wrong-URL id could not have passed. All 28 resolved; none was wrong.
`extensions.yml` now carries measured `version`, `sha256`, `manifest_version`, `war_paths`,
`use_dynamic_url`, `content_scripts`, `cs_all_urls` and `dnr_rulesets_enabled` for each.

`python3 scripts/fetch_extensions.py --out ...` downloads, sha-verifies, CRX3-parses, injects
`key` and unpacks **all 27 pool entries with exit 0**.

**1. ~~Pool is 4, not ≥20~~ — RESOLVED.** Pool is **27**. The shipped defaults `k_min=3,
k_max=8, n_rounds=20` now build a schedule (verified: 20 rounds, k spanning 3..8). The
random-guess floor fell from **0.225 at pool=4 to 0.080 at pool=27** (300-run Monte Carlo
through the real `score_round`), so the MCC clamp no longer hands a coin-flip miner free score.

**2. Bitwarden — still unresolved, kept at the maintainer's request.** The 2026-08-25 A/B
finding stands: zero headless footprint, before and after real `click()`/`send_keys()` gestures,
and all 8 WAR entries are dynamic so there is no fallback probe. It is retained in `pool:` with
the evidence in its `notes:`. If curation cannot reproduce a signal, it is an unlearnable label
and must move to `rejected:`.

**3. `time_to_stable_ms` is `null` for all 27.** Unchanged, and now the single largest open
item. `settle_seconds` defaults to 4.0 with no evidence behind it.

**4. Store re-publishing is frequent — re-confirmed.** Grammarly moved `14.1322.0 → 14.1323.0`
in one day, then `→ 14.1324.0` two days later. Pins are refreshed to the 2026-08-27 downloads.
Budget a pin-refresh pass before every image build.

### ⛔ Remaining Step 1 work — curation against real Chrome

Ids and manifests are facts now; **signals are still predictions derived from manifest shape.**
Each must be confirmed headless, egress blocked, stable over 5 repeat runs:

- **19 of 27 have static (non-dynamic) WAR paths** — a `chrome-extension://<id>/<path>` fetch
  probe should work: DuckDuckGo, LastPass, 1Password, Dashlane, NordPass, Honey, Capital One,
  Rakuten, Grammarly, ProWritingAid, LanguageTool, Turn Off the Lights, Google Translate,
  Immersive Translate, Evernote, ColorZilla, Wappalyzer, Loom, Checker Plus.
- **8 have no usable WAR** and need a DOM/CSS/DNR signal: AdBlock Plus, Ghostery, Privacy
  Badger, Bitwarden, Dark Reader, Notion Web Clipper, JSON Formatter, Video DownloadHelper.

Four are flagged in `extensions.yml` with `***` as expected to fail curation:
- **Notion Web Clipper** — 0 WAR **and** 0 content scripts. Nothing a page can observe at all.
- **Video DownloadHelper** — 0 WAR, and its 20 content scripts are **not** on `<all_urls>`; they
  target video hosts, so a generic bait page never triggers it.
- **JSON Formatter** — 0 WAR; its `<all_urls>` content scripts only act on JSON content-type
  responses, so an HTML bait page may show nothing. Screen against a JSON route.
- **Bitwarden** — see blocker 2.

Also site-scoped and therefore weak on a generic bait page: **Rakuten** and **Checker Plus**
(`cs_all_urls: false`, but both have static WAR, so the WAR probe carries them).

**Acceptance unchanged:** ≥20 with stable headless signals. If the four flagged entries all
fail, the pool lands at 23 — still above the bar, with no re-sourcing needed.

---

## Step 2 — `scripts/fetch_extensions.py` — ✅ DONE 2026-08-24

Written and verified against the 4-extension pool:

```
OK  uBlock Origin Lite          ddkjiahejlhfcafbddmgiahcphecmpfh  v2026.820.1159
OK  Grammarly                   kbfnbcaeplbcioakkpcpgfkobkghlhen  v14.1322.0
OK  Bitwarden Password Manager  nngceckbapebfimnlniiiahkandclblb  v2026.8.0
OK  Dark Reader                 eimadpbcbfnmbkopoojfekhnkhdbieeh  v4.9.129
```

All four injected keys verified to reproduce their real store IDs. Failure paths tested:
MV2 pool exits 1; sha256 drift exits 1 with both hashes printed.

Also done in this pass:
- `extensions.yml` — 4 verified entries plus a `rejected:` section so uBlock Origin (MV2) does
  not get re-added
- `api/core/configs/_challenge.py` — `ChallengeConfig` + `BrowserConfig`, wired into `MainConfig`;
  `ENV_PREFIX_CHALLENGE` added to constants
- `api/endpoints/challenge/_payload_manager.py` — schedule, MCC, `PayloadManager`, single-flight
  guard
- `tests/test_payload_manager.py` — 30 tests. Full suite: 33 passed.
- `.gitignore` — `volumes/extensions/` and `*.crx`

**Gap found:** the scaffold's `auth.py` has **JWT only**, no `auth_api_key` / `APIKeyHeader`.
That dependency must be written before Step 5 can add auth to `/score`.

<details><summary>Original Step 2 instructions</summary>


Reads `extensions.yml`, and per extension: download `.crx` → verify sha256 → parse CRX3 →
extract the matching pubkey → unzip → inject `key` → strip `update_url` → drop
`_metadata/verified_contents.json` → assert derived ID == pinned ID.

CRX3 format: `Cr24` magic, `uint32 version` (must be 3), `uint32 header_len`, then a protobuf
header, then the ZIP payload. In the header, fields 2 (`sha256_with_rsa`) and 3
(`sha256_with_ecdsa`) each contain `AsymmetricKeyProof` messages whose field 1 is the SPKI
public key. Field 10000 is `signed_header_data`, whose field 1 is the 16-byte `crx_id`.

ID derivation:
```python
digest = hashlib.sha256(spki_der).digest()[:16]
ext_id = "".join(chr(97 + (b >> 4)) + chr(97 + (b & 0xF)) for b in digest)
```

Pick the pubkey whose derived ID equals the declared `crx_id`. Raise if none match — never
fall back to the first key.

Also: zip-slip guard on extraction, and no extension directory name may contain a comma
(`--load-extension` is comma-separated).

**Acceptance:** all pool extensions unpack; every derived ID equals its pinned ID; a Chrome
launch with 5 of them shows all 5 under their real store IDs.

</details>

---

## Step 3 — `_browser.py` — ✅ DONE 2026-08-25

Written and verified live on `mac-arm64` against the real 4-extension pool.

`api/endpoints/challenge/_browser.py` — `run_round()` is the single entry point;
`ChromeSession` is a context manager owning one round's scratch dir, browser and teardown.

```python
run_round(subset, miner_js, *, pool, page_url, settings,
          settle_seconds=4.0, script_budget_sec=10.0, round_tag=None) -> dict[str, bool]
```

`BrowserSettings` is a plain frozen dataclass, not the pydantic config, so the module imports
and tests without the app config being loaded. `BrowserSettings.from_config(config.challenge.browser)`
bridges them.

All seven Step 3 requirements are implemented: per-round writable copies of the extension dirs,
fresh `--user-data-dir`, minimal flag list, **real-store-ID load assertion before the miner runs**,
settle-then-`execute_async_script`, `quit()` + path-filtered process sweep in `close()`, and a
script timeout `_SCRIPT_TIMEOUT_MARGIN_SEC = 5.0` above the in-script budget.

**Acceptance — met.** 4 sequential rounds with different random subsets each returned one boolean
per pool extension; `pgrep 'Chrome for Testing'` = 0 and `volumes/scratch/` empty afterwards.
Per-round wall time 6.3–11.5s at `--settle 5` (the 11.5s round had all 4 extensions loaded;
uBlock Lite's DNR re-index is the variable cost, as predicted).

**Also done in this pass:**
- `challenge/scripts/run_round.py` — local dev harness. Serves the bait page over http on an ephemeral
  port, builds a schedule, runs rounds, prints ground truth beside the prediction. Not part of
  the served challenge; the API may never print ground truth.
- `tests/test_browser.py` — 30 tests over the pure surface (flag list, staging, wrapper codegen,
  prediction normalisation, lifecycle). Full suite: 60 passed.
- `volumes/chrome/` and `volumes/scratch/` added to `.gitignore`.

### Verified this pass — do not re-derive

| Fact | Detail |
|---|---|
| CfT 152.0.7977.54 `mac-arm64` | `chrome` and `chromedriver` both report the pinned version |
| selenium | `4.47.0`, matches the pin in `REFERENCE.md` §6 |
| Key injection holds live | All 4 extensions load under their **real store IDs** — Step 0's assumption 1 confirmed a second time, now through the production code path |
| `--load-extension` still honoured | Chrome 152, headless, 1–4 extensions at once |
| WAR probe works | `fetch("chrome-extension://kbfn…/src/inkwell/index.html")` resolves when Grammarly is enabled and rejects when it is not — Step 0 assumption 3 confirmed |

**The load assertion is proven, not assumed.** Negative test: a copy of Dark Reader with the
injected `key` deleted from its manifest. Chrome derived the path-based id
`lebcahmcldeencfbbcajdgniaeejlbcm` instead of the store id `eimadpbcbfnmbkopoojfekhnkhdbieeh`,
and `ChromeSession.launch()` raised `BrowserError`. This is the exact failure the assertion exists
to catch — without it that extension would have loaded fine and been a permanent silent false
negative. (`mhjfbmdgcfjbbpaeojofohoefgiehjai` also shows up in `loaded`: it is Chrome's built-in
PDF viewer component extension, always present, and must not be mistaken for a pool member.)

`run_round.py --rounds 0` is the standalone check for this — no scoring, no miner script:

```sh
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 0   # whole pool
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 0 --ext grammarly dark
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 0 --no-headless --hold 60
```

Exit 0 only if every requested extension is enabled under its real store id.

<details><summary>Original Step 3 instructions</summary>

One entry point: `run_round(subset, miner_js) -> dict[str, bool]`.

1. Copy the k selected extension dirs into per-round writable scratch (see Traps)
2. Fresh `--user-data-dir` per round
3. Launch with a **minimal** flag list + `--headless=new`
4. **Assert the k extensions loaded, by real store ID** — a mis-keyed extension otherwise
   becomes a permanent silent false negative
5. Navigate to the bait page, settle, `execute_async_script(miner_js)`
6. `driver.quit()` in `finally`, plus a process-group sweeper filtered by the round's scratch
   path (so it can't kill a concurrent round). `quit()` fails exactly when cleanup matters most
7. `set_script_timeout` must be **strictly greater** than the in-script timeout, or Selenium
   raises before our own sentinel fires and the diagnostic is lost

Pass `executable_path` explicitly so Selenium Manager never runs (it phones home).

**Acceptance:** 5 sequential rounds with different random subsets each return a dict with one
boolean per pool extension; no orphan `chrome`/`chromedriver` processes remain afterwards.

---

</details>

---

## Step 4 — Bait page — ✅ DONE 2026-08-26

`src/exc_challenge/challenge/templates/index.html` is written, in use by `run_round.py`, and
served by the app at `/_web` via `api/mount.py` (6703 bytes, HTTP 200). Served over http, never
`file://` — `web_accessible_resources` declare `matches: ["http://*/*", "https://*/*"]`, so from
a `file://` page every WAR probe is a false negative no matter what is loaded.

Contains every element the spec calls for: `<input type="password">` inside a real `<form>`,
ad-shaped divs (`#ad-banner` 728×90, `.adsbygoogle` 300×250, `.ad-slot` 320×50), a `<textarea>`
with deliberately bad grammar, a `contenteditable` div, images, external links, a fixed cookie
banner, explicit light backgrounds throughout, and a drawn-on `<canvas>`. Each element carries a
comment naming the extension class it baits.

**Zero ground truth** holds: no global, no query param, no server-rendered hint. The one global
it does set, `window.__baitReady`, is a load marker carrying no information about the enabled set.

**Non-obvious, verified:** the page must be served over **http**, not `file://`. Grammarly's
`web_accessible_resources` entry declares `matches: ["http://*/*", "https://*/*"]`, so from a
`file://` page every WAR probe is a false negative regardless of what is loaded. `run_round.py`
runs a throwaway `http.server` on an ephemeral port for exactly this reason.

**Acceptance — NOT met yet.** The "ad-shaped element is measurably blocked with an ad blocker in
the subset" assertion is not written. This is the check that catches the read-only `_metadata`
trap, and without it a broken build looks identical to a working one. See the open item under
Step 1 — right now uBlock Lite's effect on the page is unmeasured, which is the same gap.

<details><summary>Original Step 4 instructions</summary>

`src/exc_challenge/challenge/templates/index.html`, served at `/_web`.

A blank page gives extensions nothing to react to. Include: `<input type="password">` inside a
real `<form>`, ad-shaped divs (`#ad-banner`, `.adsbygoogle`, 728×90, 300×250), a `<textarea>`,
images, external links, a cookie-consent banner, light backgrounds, a `<canvas>`.

**Zero ground truth**: no enabled-set global, no query param, no server-rendered hint. Generate
script tags from config rather than hardcoding them (hardcoded tags drifting from config is a
live bug in ADA3's `index.html`).

**Acceptance:** with an ad blocker in the subset, an ad-shaped element on the page is measurably
blocked or hidden. **This is also the check that catches the read-only `_metadata` trap** —
without it, a broken build looks identical to a working one.

---

</details>

---

## Step 5 — Scoring + API — ✅ DONE 2026-08-26

**`_payload_manager.py`** (new):
- `build_round_schedule(pool, T, k_range)` → per-round subsets. Use `secrets`, not `random`.
  This is the ground truth.
- per-round prediction recording
- `calculate_score()` → `mean(max(0, MCC))`, constants at module top

```
MCC = (TP·TN − FP·FN) / sqrt((TP+FP)(TP+FN)(TN+FP)(TN+FN))
      → 0.0 when the denominator is 0 (miner answered all-one-class)
```

**`service.py`**: orchestration only — build schedule, loop rounds via `_browser.run_round()`,
record, return `calculate_score()`. **Never computes the metric itself** (matches ADA3, where
`service.py` is 251 lines and contains no scoring). Keep it `def`, not `async def`.
Per-round `try/except` → that round scores 0, the run continues.

**`schemas.py`**: exactly one file named `solution.js`, ≤500 lines (validator already exists).
`MinerInput` carries the published pool.

**`router.py`**: add `Depends(auth_api_key)` to `/score` — the template ships it unguarded.

**`core/configs/_challenge.py`** (new, follow flowprint's): pool path, `T`, k range, `P`,
settle seconds, script budget, api_key.

Single-flight guard on `/score` (flowprint's `ScoringStatus` pattern).

**Acceptance:** unit tests pass — MCC against known confusion matrices, the all-one-class → 0.0
edge case, and `build_round_schedule` k-range bounds. Stubs returning all-`true`, all-`false`,
and random each score ≈0. `POST /score` returns a bare float in `[0,1]`.

### Verified this pass — do not re-derive

- **All acceptance criteria met.** 79 tests green; `tests/test_challenge_score.py` covers the
  stubs (all-true → 0.0, all-false → 0.0, random → <0.35, perfect → 1.0), a failing round
  scoring 0 without aborting the run, auth, the submission-shape rules, and single-flight.
- **End-to-end against real Chrome 2026-08-26.** `POST /score` with the baseline `solution.js`,
  2 rounds, k=[1,3] → `0.5773502691896258` (= 1/√3, a real MCC) in **12.5s**, HTTP 200 with a
  bare float body. `GET /task` published exactly the 4 `pool:` ids and no `rejected:` id.
  `GET /_web/index.html` → 200. No scratch dirs and no Chrome processes left behind.
- **`auth_api_key` lives in `core/dependencies/api_key.py`, not `auth.py`.** Importing `auth.py`
  pulls `potato_util.crypto.jwt`, which raises `ImportError: cannot import name
  'AllowedPrivateKeyTypes'` against the installed PyJWT. Nothing imported `auth.py` before, so
  the breakage was invisible — `auth_jwt` is currently unusable and unrelated to `/score`.
- **`max_parallel_rounds` (P) is still unused.** Rounds run sequentially, as this step specifies.
  At ~6s per round the shipped `n_rounds=20` is roughly 2 minutes per scoring call; wiring P is
  the obvious next optimisation and belongs with Step 6.
- **`/score` cannot run on shipped defaults** — `k_max=8` against a pool of 4 raises
  `ValueError: k_max (8) exceeds pool size (4)` from `build_round_schedule()`. This is Step 1's
  blocker 1, not a Step 5 defect; the smoke test above set `k_max=3` to get around it.

---

## Step 6 — Container — ✅ DONE 2026-08-27

**Build context moved to the repo root.** `compose.yml` now sets `context: .` with
`dockerfile: ./src/exc_challenge/challenge/Dockerfile`, and `.dockerignore` moved to the root
with it. This was the blocker: `extensions.yml` and `scripts/fetch_extensions.py` live at the
root and `COPY` could not reach them from the old context.

**Platform decided by fact, not preference.** Chrome for Testing's channel list:

| Channel | Version | ships `linux-arm64`? |
|---|---|---|
| **Stable** | 152.0.7977.64 | ❌ |
| Beta | 153.0.8010.12 | ✅ (first: 153.0.8001.0) |

Our pin `152.0.7977.54` offers `linux64, mac-arm64, mac-x64, win32, win64` — no `linux-arm64`.
Native arm64 would mean **running Beta Chrome in a scoring challenge**, which is not acceptable,
so `compose.yml` pins `platform: linux/amd64`. Prod is x86; on Apple Silicon this is a QEMU tax
on local dev only.

### What the image contains

- **Stage `chrome`** — CfT 152.0.7977.54 chrome + chromedriver, each verified against a sha256
  computed from the real download (`88af8366…` / `66f3984d…`) before anything is unpacked.
- **Stage `extensions`** — runs `fetch_extensions.py` at build, so all 27 extensions are baked
  in with their `key` injected. Prod runs with egress blocked and never downloads.
- **Stage `base`** — Debian **trixie** `t64` runtime libs (`libasound2t64`, `libatk1.0-0t64`,
  `libatk-bridge2.0-0t64`, `libatspi2.0-0t64`, `libcups2t64`, `libglib2.0-0t64`,
  `libgtk-3-0t64`), plus `tini` and `xvfb`.
- **Stage `app`** — copies `api/`, **`templates/`** and **`extensions.yml`**, then runs the gates.

`tini` is PID 1 (`ENTRYPOINT ["/usr/bin/tini", "--", "docker-entrypoint.sh"]`). Chrome forks a
zygote and orphans grandchildren every round; without a reaper those accumulate until the
container hits `pids_limit` and rounds start failing for no visible reason.

Xvfb is installed but never started — running it changes Chrome's code path and invalidates the
headless/headful equivalence the labels depend on.

### Two bugs this step fixed

1. **The app could not start at all.** `api/mount.py` resolves the bait page to
   `<api_dir>/../templates`, and the old Dockerfile copied only `./api`. `StaticFiles` raises
   `RuntimeError: Directory ... does not exist` at import time, so the container would have died
   on boot. Now copied explicitly.
2. **`pool_path` pointed at a file that was never in the image.** `/app/extensions.yml` is now
   copied from the repo root.

### Build-time gates — `scripts/verify_chrome_build.py`

Run from the `app` stage; each guards a failure that is otherwise **silent at runtime**, where a
broken Chrome returns 0.0 for every miner and the container looks healthy.

| Gate | Catches |
|---|---|
| `ldd chrome` has no "not found" | a bookworm dependency list against trixie |
| `chrome --version` **and** `chromedriver --version` match the pin | a driver/browser mismatch |
| every pool id is unpacked under `/opt/extensions` | a pool entry that never made it in |
| injected `key` derives the pinned id; no commas in ids | path-derived ids, which break every `chrome-extension://` probe |
| a real headless launch loads one extension | everything above being right and Chrome still not working |

The gate script deliberately does **not** import `api.*` — the app config is absent at build
time, and a gate that imported the code under test would prove less.

### Container settings

`platform: linux/amd64`, `shm_size: 2gb`, `tmpfs: /run/exc` (`mode=1777,size=2g,exec`),
`mem_limit: 8g`, `pids_limit: 4096`, `no-new-privileges:true`, `cap_drop: ALL`.

⚠️ **`cap_drop: ALL` alone breaks the container.** `docker-entrypoint.sh` runs `chown` and then
`gosu` down to `EXC-user`, both as root, so `CHOWN`, `FOWNER`, `SETUID` and `SETGID` are added
back and nothing else. `gosu` is not setuid, so `no-new-privileges` does not interfere with it.

⚠️ **`exec` on the tmpfs is explicit** because Docker mounts `--tmpfs` `noexec` by default.

⚠️ **Prod ignores `compose.yml` entirely.** Every setting above must be mirrored into the
`challenge_container_run_kwargs` block of `active_challenges.yaml`, along with `internal: true`
on the network. Anything set only in `compose.yml` silently does not apply in production.

### Proving it without the challenge — `run_round.py`

Shipped to `/usr/local/bin/run_round.py`. Serves the bait page over loopback, launches Chrome
with a random subset loaded, runs a WAR-probe miner, and prints a per-extension truth/prediction
table. Touches no `/score`, no scheduler, no metric — so a failure points at exactly one layer.

```
docker compose exec challenge-api python3 /usr/local/bin/run_round.py -k 5 --settle 6
```

Probe paths are read live from each unpacked manifest rather than pinned in `extensions.yml`,
for the same reason the manifest facts were dropped from that file: they rot.

### Acceptance — met 2026-08-27

| Check | Result |
|---|---|
| image builds | ✅ 765MB, `amd64` |
| all 5 build gates pass | ✅ incl. a real headless launch of `aapbdbdo…` |
| `curl localhost:10001/health` | ✅ 200 |
| `GET /task` | ✅ 27 ids |
| `GET /_web/index.html` | ✅ 200, 6703 bytes |
| `POST /score` without key | ✅ 401 |
| `run_round.py -k 6` | ✅ every probeable extension classified correctly |
| **`POST /score`, 20 real Chrome rounds** | ✅ **0.6690**, 17/20 rounds completed |
| scratch dirs / Chrome processes left behind | ✅ 0 and 0 |

### ⚠️ Two caveats from the local (QEMU) run

**3 of 20 rounds failed** — two `script timeout`, one `invalid session id: session deleted as
the browser has closed the connection`. Each failed round scores 0.0, so this pulled the result
down from roughly 0.78 to 0.669. `script_budget_sec` is 10.0 and the probe issues 17 parallel
`chrome-extension://` fetches; under x86 emulation that is tight. Re-measure on real x86 before
changing the config - this may be entirely an emulation artifact. If it reproduces there,
`script_budget_sec` must rise, because a timeout is scored as a wrong answer rather than as the
infrastructure failure it is.

**`mem_limit: 8g` is not in force locally.** `docker stats` reports a 3.813GiB ceiling: the
Docker Desktop VM has less RAM than the limit asks for, so the limit silently does not bind.
`max_parallel_rounds` defaults to 4 at roughly 1GB of Chrome each, so a local run that raises it
hits the VM, not the cgroup. Size the Desktop VM before trusting any parallelism measurement
taken on this machine.

---

## WAR probing — two traps found 2026-08-27 in the container

Both were found by running `run_round.py` against the real image, and both make an extension
look **absent while it is loaded and running**. Any miner, and the curation pass, needs them.

**1. A declared web-accessible path is not proof the file exists.** ColorZilla's manifest
declares `css/content-style.css`; the extension ships only `css/page.css` and `css/popup.css`.
The probe fetched a 404 and reported a false negative. A probe path must be verified against the
unpacked extension, not read out of the manifest and trusted.

**2. Most WAR entries are patterns, not paths.** `images/*`, `*.css`, `fonts/*.woff2`. Skipping
them throws away most of the probeable surface — resolving them against real files took the
probeable count from a handful to **17 of 27**. Note that in WAR globbing `*` matches `/` as
well, so `*.css` matches `css/page.css`.

A third filter matters for correctness though it changed nothing in this pool: a WAR block whose
`matches` is scoped to specific origins cannot be fetched from the bait page, so those entries
must be skipped rather than counted as probeable.

**Result:** with all three applied, `run_round.py` classifies every probeable extension
correctly. The only misses are extensions with no static WAR path at all — in the last run
Dark Reader, Ghostery and Privacy Badger, each of which needs a DOM, CSS or DNR signal instead.

**17 of 27 are WAR-probeable.** The other 10 are where the actual difficulty of this challenge
lives.

---

## ⚠️ Open finding — `interact()` is dead code in the scoring path

Found 2026-08-27 while wiring Step 6. `run_round()` is:

```python
with ChromeSession(settings, tag) as session:
    session.launch(sorted(subset))
    session.open_page(page_url, settle_seconds)
    return session.run_script(miner_js, pool, script_budget_sec)
```

**`session.interact()` is never called.** The gesture script exists precisely because "some
extensions — password managers above all — only inject after a real user gesture on the field
they care about, so this is a detection surface, not decoration" (`_browser.py`). The 2026-08-25
Bitwarden A/B used `interact()`; the production scoring path does not.

This was a minor gap against a pool of 4. Against the current pool it covers **5 of 27 labels**
— Bitwarden, LastPass, 1Password, Dashlane, NordPass — which may be unlearnable for no reason
other than that nobody clicks the password field.

Not fixed here, because it changes what `/score` measures and that is a deliberate call, not a
side effect of containerisation. The fix is roughly:

```python
session.open_page(page_url, settle_seconds)
session.interact()
time.sleep(post_gesture_settle_sec)   # needs a new config field
```

The Bitwarden investigation used 6s before the gestures and 4s after, so a post-gesture settle
is required — reusing `settle_seconds` would roughly double round wall-time. Decide the knob
before curation measures `time_to_stable_ms`, or those measurements will be taken against a
page state that scoring never reproduces.

`interact()` runs on every scored round (see C3), and `run_round.py` exercises it too.

---

## Step 7 — Miner-facing

Contract — `solution.js` defines:
```js
window.detect_extensions = async function () {
  return { "<extensionId>": true /* or false */, /* ... */ };
};
```
Missing keys are treated as `false`.

Write `examples/miner_commit/src/commit/solution.js` as a real baseline that detects 2–3 easy
extensions by WAR probing, so miners have a working starting point.

**Acceptance:** build the miner container, `POST /solve` on :10002, feed its output straight
into `/score`, get a score clearly above 0 and clearly below 1.

---

## Step 8 — Scaffold bug fixes — ✅ DONE 2026-08-26

- `myhub/rest-EXC-commit` is an **invalid Docker reference** (uppercase) — in
  `examples/miner_commit/compose.yml:3`, `examples/miner_commit/scripts/build.sh:31`, its
  README, and `templates/compose/compose.override.dev.yml:27`. Rename to
  `redteamsubnet61/submission-exc-challenge`.
- `.vscode/settings.json:155` — un-rendered `src/my_challenge/challenge` path.
- `pyproject.toml:65-69` — `[project.urls]` point at `challenge-template`; `"template"` keyword
  at line 23.
- ~~`volumes/configs/rest-exc-challenge/` is empty~~ — **fixed 2026-08-26.** The five template
  files are installed, plus a new `challenge.yml` (without which `config.challenge` ran entirely
  on pydantic defaults). `scripts/install-configs.sh` does the copy; the Dockerfile never
  installs configs, so the mount in `compose.yml` is the only source in local dev.

- ⚠️ **YAML silently beats environment variables.** `config.py` reads every yml into a dict and
  passes it to `MainConfig(**_config_dict)`. Pydantic-settings ranks `init_settings` **above**
  `env_settings`, so any key present in a yml file makes the corresponding env var dead.

  | config dir | `EXC_CHALLENGE_API_PORT=9999` | result |
  |---|---|---|
  | mounted (api.yml sets `port: 10001`) | set | **10001** — env ignored |
  | absent | set | 9999 |

  This is pre-existing scaffold behaviour, not introduced by the challenge work, and it is
  already live: `compose.yml` sets `EXC_CHALLENGE_API_PORT` in `environment:` *and* mounts the
  config dir, so changing that variable moves the published port mapping while the app keeps
  binding 10001. **Local dev and prod therefore resolve config differently** — prod gets no
  config mount and passes env via `challenge_container_run_kwargs`, where env does win. Anything
  that must be overridable at runtime has to be left out of the yml files; that is why
  `challenge.yml` omits `api_key`.

**Acceptance:** `tests/test_challenge_api.py` and `tests/test_miner_commit_api.py` stay green.

All four fixed 2026-08-26; 79 tests green. Image reference is now
`redteamsubnet61/submission-exc-challenge`; `templates/configs/challenge/*.yml` are installed
into `volumes/configs/rest-exc-challenge/`, so the container no longer runs on bare pydantic
defaults.

---

## Reference: `ada-detection-challenge` — reviewed 2026-08-26

Read before Step 6. `../ada-detection-challenge` is the same cookiecutter lineage and is the
closest thing to a working precedent.

**Identical, do not re-derive.** `compose.sh` is byte-identical. `build-docker.sh` and the
`Dockerfile` differ *only* in `ADA_`→`EXC_` naming and slugs — same 3-stage build, same
`python:3.10-slim-trixie` base, same entrypoint, same `/etc/<slug>` config mount.

**ADA runs no browser in its container** — and this is the one place we cannot copy it. Its
Dockerfile installs no Chrome, Selenium, tini or Xvfb, and its compose sets no `shm_size`,
`tmpfs` or `mem_limit`. Browser work is delegated to an external **bot-runner** service
(`_bot_runner.py` → `POST /api/runs`, then poll). That service's payload is
`{bot, driver_preset, device_type, url, count, headless, metadata}` — **no extension parameter**,
and there is no extension handling anywhere in the ADA repo. Loading a different random subset
per round needs `--load-extension` and a fresh profile, so Chrome has to live in our image.
Decision 2026-08-26: embed Chrome, Step 6 proceeds as written.

**Worth copying:**
- ADA pins `platform: linux/amd64` in `compose.yml`. We have not — see the open question below.
- ADA's `ChallengeConfig` uses `FrozenBaseConfig` with required fields
  (`api_key: SecretStr = Field(..., min_length=12)`), so no working key ships in the image. Ours
  still defaults to `challenge_api_key`. **Open — deliberately deferred 2026-08-26.**
- `auth_api_key` belongs in `core/dependencies/auth.py`. Adopted 2026-08-26: our JWT-flavoured
  `auth.py` is replaced by the API-key version, which also removes the
  `potato_util.crypto.jwt` import that raised `ImportError: cannot import name
  'AllowedPrivateKeyTypes'` against the installed PyJWT.

**Deliberately different:** ADA serves `/_web` as a Jinja route because it injects per-session
context. Ours must inject nothing — zero ground truth reachable from the page — so a
`StaticFiles` mount is used instead, precisely because it cannot leak.

### ⚠️ Open before Step 6 can be verified

`compose.yml` has no `platform:` pin, so a build on Apple Silicon defaults to `arm64`, and CfT
ships no `linux-arm64` before v153 — Chrome will not run in that image. Either pin
`platform: linux/amd64` as ADA does (and accept QEMU), or build on x86.

---

## Out of scope

`src/exc_challenge/controller.py` and `src/exc_challenge/challenge_manager.py`. They are
validator-side plugins loaded by `scoring-api` via `active_challenges.yaml`; nothing here
imports them. See the dedicated section in `design.md` for what's in them and when it matters.

## Final verification

1. Determinism — score the same submission 5×; MCC variance should be small. If it's high,
   **raise the settle window**, don't bump `T`.
2. Settle window — set it to the p99 of `time_to_stable_ms` across the pool, floor 2s, cap 8s.
3. End-to-end — `GET /health`, `GET /task`, `POST /score` with
   `{"miner_input": ..., "miner_output": ...}` and `X-API-Key`, asserting a bare float in `[0,1]`.
