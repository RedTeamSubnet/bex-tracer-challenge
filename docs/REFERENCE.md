# Technical Reference — self-contained

**Purpose.** Everything needed to build this challenge, verbatim, so an executing model needs
**no web search and no exploration of sibling repos**. Every fact here was verified against live
endpoints or Chromium source on 2026-08-24.

If something you need is not here, that is a gap in this document — prefer asking over guessing,
because most wrong answers in this problem space fail *silently*.

> **Historical snapshot — do not trust the "current repo state" sections.**
> Written 2026-08-24 to drive the initial build. The Chrome, CRX3 and Selenium facts in
> sections 1–4 still hold. Everything describing *this repo* has drifted: `/score` now has an
> auth dependency, `MinerInput` carries the pool and its groups, `submission_file_name` no
> longer exists, and the single `solution.js` has been replaced by one file per group.
> For today's contract read [`README.md`](./README.md); for the code, read the code.

---

## 1. Verified environment facts

```text
Chrome for Testing Stable = 152.0.7977.54     (as of 2026-08-24)
Chrome for Testing Beta   = 153.0.8010.5
```

Platforms per channel:

- **Stable 152**: `linux64`, `mac-arm64`, `mac-x64`, `win32`, `win64` — **no `linux-arm64`**
- **Beta 153**: adds `linux-arm64`

Version discovery (use to *pick* a pin; never resolve at build time):

```text
https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json
```

Binary download pattern:

```text
https://storage.googleapis.com/chrome-for-testing-public/{VERSION}/{PLATFORM}/{BINARY}-{PLATFORM}.zip
# BINARY in {chrome, chromedriver}   -- never chrome-headless-shell
```

macOS: after unzipping run `xattr -dr com.apple.quarantine <dir>` or the binaries will not
launch. Chrome binary path inside the mac zip:

```text
chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing
```

CRX download from the Chrome Web Store:

```text
https://clients2.google.com/service/update2/crx?response=redirect&acceptformat=crx2,crx3&prodversion={VERSION}&x=id%3D{EXT_ID}%26uc
```

Returns 302 to `clients2.googleusercontent.com`, `content-type: application/x-chrome-extension`.

---

## 2. Why `--load-extension` works on Chrome for Testing

Chromium `main`, `chrome/browser/extensions/extension_service.cc`:

```cpp
void ExtensionService::LoadExtensionsFromCommandLineFlag(const char* switch_name) {
  if (switch_name == switches::kLoadExtension) {
#if BUILDFLAG(GOOGLE_CHROME_BRANDING) && !BUILDFLAG(IS_CHROMEOS)
    LOG(WARNING) << "--load-extension is not allowed in Google Chrome, ignoring.";
    return;
#else
    if (safe_browsing::IsEnhancedProtectionEnabled(*profile_->GetPrefs())) { return; }
    if (ShouldBlockCommandLineExtension(*profile_)) { return; }
#endif
  }
```

A **compile-time buildflag**, not a runtime feature. Chrome for Testing is built with
`is_chrome_branded=false`, so the `#else` branch compiles in. This is also why the Chrome 142
change removed the `--disable-features=DisableLoadExtensionCommandLineSwitch` workaround — that
feature flag no longer exists in `extension_features.cc`.

Two runtime gates survive in the non-branded branch:

| Gate | Pref | Action |
| --- | --- | --- |
| Enhanced Safe Browsing | `safebrowsing.enhanced` | Must be `false`. Default is `false`; assert it |
| Policy `ExtensionInstallTypeBlocklist` with `"command_line"` | — | Ensure `/etc/opt/chrome/policies/` absent in image |

---

## 2b. Manifest V2 is hard-rejected (verified 2026-08-24)

Chrome for Testing 152 **refuses to load MV2 extensions entirely.** Not deprecated, not
warned — rejected at load time:

```text
WARNING:extensions/browser/load_error_reporter.cc:73] Extension error: Failed to load extension
from: .../unpacked/ublock. Cannot install extension because it uses an unsupported manifest version.
```

The extension does not appear in `chrome://extensions-internals/` at all — not even as disabled.
Loaded MV3 extensions carry a `REQUIRE_MODERN_MANIFEST_VERSION` creation flag.

**Consequence:** the pool can contain **MV3 only**. Screen `manifest_version == 3` before
including any candidate. Notably this rules out **uBlock Origin proper** (`manifest_version: 2`),
the single most-cited extension-fingerprinting target — use uBlock Origin Lite instead.

## 3. Extension IDs — the rule

The ID of an unpacked extension is:

1. **If `manifest.json` has a `key`** — `SHA256(base64_decode(key))[:16]`, nibbles `0-f` to `a-p`
2. **Otherwise** — the same over `SHA256(absolute_resolved_directory_path)`

`LoadFromCommandLine` calls `base::MakeAbsoluteFilePath()`, which resolves symlinks, so the
realpath is the input in case 2.

**Store CRX manifests contain no `key` field.** Verified empirically against a live store CRX
(uBlock Origin Lite): `HAS key field: False`. The Web Store rejects uploads containing `key`;
Chrome injects it at install time in `SandboxedUnpacker::RewriteManifestFile()`. Loading unpacked
bypasses that, so **we must inject it ourselves**.

### The multi-key gotcha

CRX3 headers carry **several** `AsymmetricKeyProof` entries. A live sample had three:

```text
proof field 2 pubkey len 294 -> id lfoeajgcchlidpicbabpmckkejpckcfb   <-- NOT the extension
proof field 2 pubkey len 294 -> id ddkjiahejlhfcafbddmgiahcphecmpfh   <-- the real one
proof field 3 pubkey len  91 -> id gbphpckglpmphemnalmbpocejhmmjlae   <-- NOT the extension
```

Taking the first gives the wrong ID **silently**. Disambiguate against
`signed_header_data.crx_id` (header field 10000, `SignedData` field 1, exactly 16 bytes).

### `use_dynamic_url` — measured, not hypothetical

Verified 2026-08-24 against uBlock Origin Lite: **all 5** of its `web_accessible_resources`
entries set `use_dynamic_url: true`. Chrome then serves those resources under a per-session
random GUID instead of the store ID, and the GUID rotates on every browser launch:

```js
fetch('chrome-extension://ddkjiahejlhfcafbddmgiahcphecmpfh/web_accessible_resources/noop.js')
  -> TypeError: Failed to fetch          # real store ID

fetch('chrome-extension://c188519d-81ef-4a0c-8a4d-62e15dd9bbb4/web_accessible_resources/noop.js')
  -> {ok: true, status: 200}             # per-session GUID, different every launch
```

The GUID is visible in `chrome://extensions-internals/` as `e["guid"]` (distinct from `e["id"]`),
but **page JavaScript cannot enumerate it** — that is the entire point of the feature.

So WAR probing is dead for any extension setting this. The fetch *mechanism* is sound; the
extension is deliberately resisting it. Expect this to be **common among MV3 ad-blocker-class
extensions**, not rare. Record it per extension and lean on DOM/CSS/behavioural signals instead.

**Probe from a real `http://` origin.** A `data:` URL page has an opaque origin that WAR
`matches` patterns do not satisfy, and gives the same `TypeError: Failed to fetch` for an
unrelated reason — which will mislead your diagnosis.

---

## 4. CRX3 parser — working code

Layout: `Cr24` magic, `uint32 version` (3), `uint32 header_len`, protobuf header, ZIP payload.

```python
from __future__ import annotations
import base64, hashlib, io, json, struct, zipfile
from pathlib import Path

_CRX_MAGIC = b"Cr24"

def _varint(buf: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        b = buf[i]; i += 1
        r |= (b & 0x7F) << s
        if not b & 0x80:
            return r, i
        s += 7

def _fields(buf: bytes):
    """Minimal protobuf wire-format scanner: yields (field_number, value)."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 2:
            ln, i = _varint(buf, i); v = buf[i:i + ln]; i += ln
        elif wt == 5:
            v = buf[i:i + 4]; i += 4
        elif wt == 1:
            v = buf[i:i + 8]; i += 8
        else:
            raise ValueError(f"bad wire type {wt}")
        yield fn, v

def crx_id_from_pubkey(spki_der: bytes) -> str:
    digest = hashlib.sha256(spki_der).digest()[:16]
    return "".join(chr(97 + (b >> 4)) + chr(97 + (b & 0xF)) for b in digest)

def parse_crx3(data: bytes) -> tuple[str, bytes, bytes]:
    """Returns (extension_id, spki_der_public_key, zip_bytes)."""
    magic, version, header_len = struct.unpack("<4sII", data[:12])
    if magic != _CRX_MAGIC:
        raise ValueError(f"not a CRX: {magic!r}")
    if version != 3:
        raise ValueError(f"unsupported CRX version {version}")
    header, zip_bytes = data[12:12 + header_len], data[12 + header_len:]

    declared_id = None
    pubkeys = []
    for fn, val in _fields(header):
        if fn in (2, 3):                       # sha256_with_rsa / sha256_with_ecdsa
            for f2, v2 in _fields(val):        # AsymmetricKeyProof
                if f2 == 1:
                    pubkeys.append(v2)
        elif fn == 10000:                      # signed_header_data
            for f2, v2 in _fields(val):        # SignedData
                if f2 == 1 and len(v2) == 16:
                    declared_id = "".join(
                        chr(97 + (b >> 4)) + chr(97 + (b & 0xF)) for b in v2
                    )
    if declared_id is None:
        raise ValueError("CRX3 header has no signed_header_data.crx_id")

    # CRITICAL: pick the proof whose key hashes to the declared crx_id.
    for pk in pubkeys:
        if crx_id_from_pubkey(pk) == declared_id:
            return declared_id, pk, zip_bytes
    raise ValueError(
        f"no AsymmetricKeyProof matches declared crx_id {declared_id} "
        f"(candidates: {[crx_id_from_pubkey(p) for p in pubkeys]})"
    )

def unpack_crx(crx_bytes: bytes, dest: Path, *, expected_id: str) -> str:
    ext_id, spki, zip_bytes = parse_crx3(crx_bytes)
    if ext_id != expected_id:
        raise ValueError(f"CRX id {ext_id} != pinned {expected_id}")

    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():                      # zip-slip guard
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"zip traversal: {info.filename}")
        z.extractall(dest)

    for junk in ("_metadata/verified_contents.json", "_metadata/computed_hashes.json"):
        (dest / junk).unlink(missing_ok=True)
    md = dest / "_metadata"
    if md.is_dir() and not any(md.iterdir()):
        md.rmdir()

    mpath = dest / "manifest.json"
    # utf-8-sig: real store manifests ship BOMs; a plain utf-8 read will fail on some
    manifest = json.loads(mpath.read_text(encoding="utf-8-sig"))
    manifest["key"] = base64.b64encode(spki).decode("ascii")   # <-- the whole point
    manifest.pop("update_url", None)
    mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    assert crx_id_from_pubkey(spki) == expected_id
    return ext_id
```

---

## 5. Chrome flags

### Safe baseline

```python
_BASE_ARGS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--window-size=1920,1080",
    "--force-device-scale-factor=1",
    "--hide-scrollbars",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-search-engine-choice-screen",
    "--disable-notifications",
    "--disable-infobars",
    "--disable-popup-blocking",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-ipc-flooding-protection",
    "--enable-automation",
    "--metrics-recording-only",
    "--disable-breakpad",
    "--noerrdialogs",
    "--password-store=basic",
    "--use-mock-keychain",
    "--disable-features=Translate,OptimizationHints,MediaRouter,"
    "InterestFeedContentSuggestions,CalculateNativeWinOcclusion,BackForwardCache",
    "--lang=en-US",
    "--accept-lang=en-US,en",
]
```

### Never pass these

| Flag | Why |
| --- | --- |
| `--disable-extensions` | Only `--disable-extensions-except` grants an exemption. `--load-extension` extensions get **none** — they load, then get disabled |
| `--disable-component-extensions-with-background-pages` | Kills component extensions some real extensions depend on |
| `--incognito` / `--guest` | Extensions not enabled unless individually allowed |
| `--single-process` | Breaks MV3 service workers |
| `--headless=old` | Removed in Chrome 132; cannot load extensions |

### Conditional

- **`--disable-background-networking`** — ChromeDriver injects it unconditionally; you cannot
  avoid it without `excludeSwitches`. Known to cause multi-second XHR stalls. If extension
  network activity hangs, remove it via `excludeSwitches`.
- **`--disable-dev-shm-usage`** — prefer `shm_size: 2gb` on the container and omit this flag.
  Keep as an env-gated fallback only.

ChromeDriver *comma-merges* `--disable-features` rather than overwriting
(`kMultivaluedSwitches` in `capabilities.cc`), so your values are safely appended to its own.

---

## 6. Selenium

Latest is `4.47.0`. Pin it. Always pass `executable_path` so Selenium Manager never runs.

```python
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

opts = Options()
opts.binary_location = CHROME_BIN
for a in _BASE_ARGS:
    opts.add_argument(a)
opts.add_argument(f"--user-data-dir={profile}")
opts.add_argument("--profile-directory=Default")
opts.add_argument("--load-extension=" + ",".join(str(d.resolve()) for d in ext_dirs))

service = Service(
    executable_path=CHROMEDRIVER_BIN,
    popen_kw={"start_new_session": True},   # own process group, for killpg cleanup
)
driver = webdriver.Chrome(service=service, options=opts)
```

### Do NOT use

- `options.add_extension(".crx")` — ChromeDriver unpacks to a temp dir **it** owns. We need path
  control because Chrome rewrites `_metadata/` inside the extension dir on every load.
- `options.enable_webextensions = True` / `driver.webextension.install(...)` — Selenium's own
  docstring: *"Enabling --remote-debugging-pipe makes the connection b/w chromedriver and the
  browser use a pipe instead of a port, disabling many CDP functionalities."* It also installs
  post-startup (race) and uses `kUnpacked` location, which **hard-errors on illegal filenames**
  that real store CRXs routinely contain — `--load-extension` (`kCommandLine`) tolerates them.

### Verifying extensions loaded

```python
driver.get("chrome://extensions-internals/")
loaded = {e["id"] for e in json.loads(driver.find_element("tag name", "pre").text)}
missing = set(expected_ids) - loaded
if missing:
    raise RuntimeError(f"extensions failed to load or IDs drifted: {sorted(missing)}")
```

If the DOM shape differs on 152, fall back to parsing `<profile>/Default/Preferences` →
`extensions.settings`.

### Running the miner script

The miner's code is **not injected**. It is staged to
`templates/static/detections/<submission_file_name>` and pulled in by the bait page's own
`<script src>`, so it loads before the extensions act and `window.detect_extensions` is
already defined by the time the wrapper runs. The wrapper only *invokes* it:

```python
wrapped = f"""
    const done = arguments[arguments.length - 1];
    const t = setTimeout(() => done({{__timeout: true}}), {int(timeout_s * 1000)});
    (async () => {{
        try {{
            if (typeof window.detect_extensions !== "function") {{
                clearTimeout(t); done({{__error: "no window.detect_extensions"}}); return;
            }}
            const r = await window.detect_extensions();
            clearTimeout(t); done({{ok: r}});
        }} catch (e) {{
            clearTimeout(t); done({{__error: String(e && e.stack || e)}});
        }}
    }})();
"""
result = driver.execute_async_script(wrapped)
```

`driver.set_script_timeout()` must be **strictly greater** than the in-script timeout, or
Selenium raises before the sentinel fires and the diagnostic is lost.

### Whose fault was it? — the rule the wrapper buys us

`/score` returns a single float and has no channel for "our side broke", so an
infrastructure failure and a bad submission both arrive at the validator as a low number.
Getting this wrong costs a miner real emissions, so the blame split is load-bearing:

| Outcome | Raised as | Blame |
| --- | --- | --- |
| `{ok: …}` | — | scored normally |
| `{__error: …}` / `{__timeout: true}` | `BrowserError` | **the miner** — scores 0, run continues |
| exception out of `execute_async_script` | `BrowserInfraError` | **us** — counts toward `_MAX_SETUP_FAILURE_RATIO` |

The wrapper is what makes the third row unambiguous: because it catches the miner's own
throws and hangs *inside the page* and returns them as a payload, **any** exception
escaping `execute_async_script` is the renderer dying, never the submission. `tab crashed`
is what shm exhaustion under concurrency looks like.

So the call must be wrapped, exactly like `driver.get()`:

```python
try:
    result = driver.execute_async_script(wrapped)
except WebDriverException as err:
    raise BrowserInfraError(f"browser died running the script: {err}") from err
```

Without it the exception escapes as a bare `WebDriverException`, which is not a
`BrowserError` at all — so `service.py` books it against the miner and the guard that
exists for precisely this case never fires. Measured before the fix: at
`max_parallel_rounds=4`, 3 of 4 rounds died this way and `/score` still returned `0.165`.

---

## 7. The `_metadata` writability trap

`extensions/common/file_util.cc`:

```cpp
void MaybeCleanupMetadataFolder(const base::FilePath& extension_path) {
  for (const auto& file : GetReservedMetadataFilePaths(extension_path))
    base::DeletePathRecursively(file);
}
```

`unpacked_installer.cc` calls this on **every** unpacked/command-line load, unconditionally.
`GetReservedMetadataFilePaths` includes `_metadata/generated_indexed_rulesets`.

Consequences:

1. **Static DNR rulesets are re-indexed on every launch** and cannot be pre-warmed. For a
   uBlock-class extension that is 1–4s per round — the dominant cost.
2. **Extension directories must be writable.** Read-only means the delete and the ruleset write
   both fail (return values ignored), so static DNR rules **silently do not apply** — ad blockers
   become no-ops, labels are wrong, and **no error is raised**.
3. Two concurrent rounds sharing an extension dir race on `_metadata/`.

**Required mitigation:** copy the k selected extension dirs into per-round writable scratch.
Because `key` is injected, the path change does not change the ID.

---

## 8. Subset selection vs profile state

`chrome/browser/extensions/installed_loader.cc`:

```cpp
// Skip extensions that were loaded from the command-line because we don't
// want those to persist across browser restart.
if (info.extension_location == mojom::ManifestLocation::kCommandLine)
  continue;
```

Command-line extensions are **not** resurrected from prefs at startup, which is what makes
per-round subset selection work. If a profile were warmed using `kUnpacked` (e.g. BiDi install),
`InstalledLoader` **would** reload all of them every launch and silently destroy subset
selection. The current design uses a fresh profile per round, so this is not an active risk —
but do not optimise toward a warmed profile without re-reading this.

---

## 9. MCC

```python
import math

def mcc(tp: int, tn: int, fp: int, fn: int) -> float:
    num = (tp * tn) - (fp * fn)
    den = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
    if den == 0:
        return 0.0          # miner answered all-one-class
    return num / den

# score_round = max(0.0, mcc(...))
# score_run   = sum(round_scores) / len(round_scores)
```

All-true, all-false and random strategies all score ~0. The validator feeds this into a
temperature softmax, so the metric must **spread** miners, not saturate.

---

## 10. Current repo state

> **Stale as of 2026-08-29.** The code blocks below are the *pre-implementation* scaffold, kept
> only to show what was replaced. `service.py`, `schemas.py` and `router.py` have all since been
> rewritten — read the files, not this section.

### `api/endpoints/challenge/service.py` — the stub that was replaced

```python
import random

from pydantic import validate_call

from .schemas import MinerInput, MinerOutput


def get_task() -> MinerInput:
    return MinerInput()


@validate_call
def score(request_id: str, miner_output: MinerOutput) -> float:

    _score_result = random.random()  # nosec B311
    return _score_result


__all__ = [
    "get_task",
    "score",
]
```

### `api/endpoints/challenge/schemas.py` — current

`MinerInput` has only `random_val` (`default_factory=gen_random_string` from
`potato_util.generator`). `CommitFilePM` has `file_name` (4–64 chars) and `content` (min 2).
`MinerOutput.commit_files` is an unbounded list with a `_check_commit_files` validator enforcing
≤500 lines per file. **Reuse that validator**; add the `solution.js` filename restriction and
put the published pool into `MinerInput`.

### `api/endpoints/challenge/router.py` — current

`GET /task` and `POST /score` exist. `post_score(request, miner_input, miner_output)` returns a
bare float; `miner_input` is accepted but unused. **`/score` has no auth dependency.**

**Correction (verified 2026-08-24):** `api/core/dependencies/auth.py` exists but ships **JWT
bearer auth only** — it exports `auth_jwt`, `get_user_id`, `is_auth`, `AuthScopeDep` and has no
`auth_api_key` and no `APIKeyHeader` anywhere in the scaffold. The validator sends
`X-API-KEY`, so an `auth_api_key` dependency must be **written**, not just imported. Model it on
ADA3's: `APIKeyHeader(name="X-API-Key", auto_error=False)`, length 8-128, alphanumeric+hyphen,
compared against `config.challenge.api_key`.

### Built already (Steps 0-4, verified)

- `extensions.yml` — 4 verified MV3 extensions, real sha256 pins.
- `scripts/fetch_extensions.py` — downloads, verifies, unpacks, injects `key`. Exits 1 on MV2 or
  sha drift.
- `api/core/configs/_challenge.py` — `ChallengeConfig` + `BrowserConfig`, wired into `MainConfig`
  as `config.challenge`. `ENV_PREFIX_CHALLENGE` added to `core/constants/_base.py`.
- `api/endpoints/challenge/_payload_manager.py` — `build_round_schedule()`, `mcc()`,
  `score_round()`, `PayloadManager`.
- `api/endpoints/challenge/_browser.py` — `run_round()`, `ChromeSession`, `BrowserSettings`,
  `wrap_miner_script()`, `normalize_predictions()`. **Verified live on mac-arm64 2026-08-25.**
- `challenge/templates/index.html` — bait page. Written, used by `run_round.py`, and mounted at
  `/_web` by `api/mount.py`.
- `scripts/run_round.py` — shared dev plumbing: `Extension` / `Pool` (id and display-name
  resolution lives here, not at print sites), `bait_server()` context manager, `build_settings()`,
  `base_parser()`.
- `scripts/run_round.py --rounds 0` — "do the extensions load, under their real store ids?"
  `--interact` drives the page, `--no-headless --slow --hold` to watch. Dev only.
- `challenge/scripts/run_round.py` — "can the miner tell?" Runs N scored rounds via `RoundRunner`,
  prints ground truth beside prediction. Dev only.
- `examples/miner_commit/src/commit/solution.js` — baseline miner, 93 lines.
- `tests/` — 79 passing across `test_payload_manager.py`, `test_browser.py`,
  `test_challenge_score.py` and the two inherited scaffold suites.

### Files that do NOT exist yet

None. `auth_api_key` now lives in `api/core/dependencies/auth.py` (which replaced the scaffold's
unused JWT machinery) and the `/_web` mount is in `api/mount.py`.

---

## 10b. Live measurements — 2026-08-25

Taken through the production `_browser.run_round()` path, headless, mac-arm64, CfT
152.0.7977.54, selenium 4.47.0. **These are measured, not assumed.**

### Per-round cost

| Extensions loaded | Wall time @ `--settle 5` |
| --- | --- |
| 2 (no uBlock Lite) | 6.3 – 6.9s |
| 2–3 (with uBlock Lite) | 7.0 – 8.1s |
| 4 (all) | 11.5s |

uBlock Lite's DNR re-index is the variable cost, exactly as §7 predicts. At `T=20` and `P=4`
that projects to roughly 40–60s per `/score` call for a 4-extension pool; a 30-extension pool
with the same `k∈[3,8]` will not be much worse, since cost tracks `k`, not pool size.

### Signal status per extension

| Extension | Claimed signal | Measured headless | Baseline miner |
| --- | --- | --- | --- |
| Grammarly | `war`, `content_script` | ✅ **both** — `src/inkwell/index.html` fetches, and it opens a `<grammarly-desktop-integration>` shadow host | 4/4 correct |
| Dark Reader | `css`, `content_script` | ✅ body background measurably changes | 4/4 correct |
| uBlock Origin Lite | `dnr` | ⚠️ **unmeasured** — needs the Step 4 blocked-ad assertion | always false (by design) |
| Bitwarden | `content_script` | ❌ **zero footprint** | always false |

### WAR probing — two traps

1. **Manifest globs are not fetchable paths.** Grammarly's manifest lists `src/images/*.png`,
   `src/js/*.js` and so on. A probe needs a **concrete file that exists in the unpacked
   extension**. Verified working: `src/inkwell/index.html` (listed literally) and
   `src/icon/app/icon-48.png`. Note the images actually live under hashed subdirectories
   (`src/images/<16-hex>/name.png`), so the glob resolves but is unguessable from outside.
2. **`matches` gates the probing page's scheme.** Grammarly's WAR entry declares
   `matches: ["http://*/*", "https://*/*"]`. From a `file://` bait page every WAR probe returns
   false regardless of what is loaded. Serve the bait page over http.

### Baseline miner scores

`examples/miner_commit/src/commit/solution.js` scores **0.38 – 0.48** over 3–4 rounds against
the 4-extension pool — above 0, well below 1, which is the Step 7 acceptance shape. It detects
Grammarly by WAR and Dark Reader by computed background, and deliberately leaves uBlock Lite at
`false` since only behavioural probing can find it.

### Store re-publish cadence

Grammarly moved `14.1322.0 → 14.1323.0` **one day** after being pinned. `fetch_extensions.py`
caught the sha256 drift and exited 1 with both hashes printed, as designed. Budget a pin-refresh
pass before every image build; do not treat a drift failure as a bug in the fetch script.

---

## 11. Sibling-challenge patterns

**Do not read those repos — the relevant patterns are here.**

- **ADA3** (`ada-detection-challenge`): `service.py` is 251 lines of orchestration containing
  **no scoring** — it calls `payload_manager.calculate_score()`. `_payload_manager.py` (300
  lines) holds `build_run_schedule()`, ground truth, predictions, `calculate_score()` and its
  private `_score_*` helpers, plus scoring constants at module top. `_bot_runner.py` holds the
  "how we drive the browser" layer. ADA3 has `tests/test_run_schedule.py` testing schedule
  generation in isolation — mirror that.
- **flowprint**: same split; `payload_managers.py` holds `calculate_score()` (macro-F1). Also has
  `core/configs/_challenge.py` for challenge-specific config, and a `ScoringStatus` single-flight
  guard so concurrent `/score` calls cannot interleave. Its sandbox container runs on a Docker
  network created with `internal=True` — the house pattern for blocking egress.
- **Both** keep `score` a sync `def`. FastAPI runs sync handlers on the threadpool; `async def`
  blocks the event loop for the whole multi-minute run.

**ADA3 mistakes not to copy:** `/_payload` is unauthenticated and accepts an attacker-chosen
`order_number`; `index.html` hardcodes script tags that drift from config; `payload_manager` is a
module-level singleton with no concurrency guard.

---

## 12. Validator contract (`redteam_core`)

- Challenge container listens on **port 10001**.
- `GET /health` → 200 (liveness gate, polled every 5s).
- `GET /task` → JSON object, used as `miner_input`. Retried 3×, **no timeout**.
- `POST /score` body `{"miner_input": {...}, "miner_output": {...}}` → **bare JSON number**.
  Non-numeric is coerced to `0.0`. **No client timeout** — ADA3 legitimately runs 277s.
- Miner container on port 10002, `POST /solve`, returns `MinerOutput`.
- Scoring headers typically `{"X-API-KEY": "..."}`.
- In production the validator launches our image via the Docker socket using
  `challenge_container_run_kwargs` from `active_challenges.yaml`. **`compose.yml` is ignored in
  prod** — hardening settings must appear in both.
