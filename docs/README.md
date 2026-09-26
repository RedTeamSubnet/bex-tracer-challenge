# Extension Classification Challenge — Docs

A RedTeam Subnet challenge where miners submit JavaScript that fingerprints **which browser
extensions are active** in a Chrome session.

The harness launches Chrome for Testing with a random subset of extensions drawn from a published
pool, loads a static bait page, runs the miner's script from page context, and scores how
accurately it identified what was installed.

## Contents

| Document | What's in it |
|---|---|
| [`overview.html`](./overview.html) | One-page summary with current numbers — open in a browser |
| [`pipeline.html`](./pipeline.html) | Pool-to-score pipeline diagram |
| [`architecture.excalidraw`](./architecture.excalidraw) | Flow diagram — open at [excalidraw.com](https://excalidraw.com) |
| [`release-notes.md`](./release-notes.md) | Release notes |

## At a glance

| | |
|---|---|
| Threat model | Page-context JS served by the bait page; entrypoints invoked via `execute_async_script` |
| Browser | Chrome for Testing 152.0.7977.54 — headless in prod, headful for local inspection |
| Extension pool | Chrome Web Store extensions, published to miners **by name** — ids are never sent |
| Current defaults | 89 extensions · 8 groups · 12 enabled · 6 sequential rounds |
| Selection | Fixed `k`, redrawn w/ coverage weighting and never revealed |
| Network | Not restricted by Compose; the bait page itself uses private loopback HTTPS |
| Miner output | Exactly one file per group, each `{extensionName: true\|false}` for that group's names |
| Metric | Mean of per-round MCC over all 89 labels, `max(0, mcc)` → `[0,1]` |

## Running it locally

Selenium lives in `src/bex_tracer/challenge/api/endpoints/challenge/_browser.py`.
`src/bex_tracer/challenge/scripts/run_round.py` drives it
end-to-end without Docker or the API — this is the loop to use while curating the pool.

```sh
pip install selenium psutil pyyaml
pip install -r src/bex_tracer/challenge/requirements.txt

# 1. Chrome for Testing -> ./volumes/chrome  (mac-arm64 shown; use linux64 on Linux)
V=152.0.7977.54; PLAT=mac-arm64
mkdir -p volumes/chrome && cd volumes/chrome
for B in chrome chromedriver; do
  curl -sfLO "https://storage.googleapis.com/chrome-for-testing-public/$V/$PLAT/$B-$PLAT.zip"
  unzip -qo "$B-$PLAT.zip"
done
xattr -dr com.apple.quarantine . 2>/dev/null   # macOS only
cd ../..

# 2. Extension pool -> ./volumes/extensions
python3 scripts/fetch_extensions.py --out ./volumes/extensions

# 3. Enable the extensions and prove they enabled
python3 src/bex_tracer/challenge/scripts/run_round.py --rounds 0    # whole pool, headless

# 4. Scored rounds (needs a miner script; not required to check loading)
python3 src/bex_tracer/challenge/scripts/run_round.py --rounds 4
```

### Watching it work

`--no-headless` opens a real Chrome window; `--interact` makes Selenium click and type its way
through the page; `--slow` paces the steps so you can follow along.

```sh
# the full show: visible window, Selenium driving, one step per second
python3 src/bex_tracer/challenge/scripts/run_round.py --rounds 0 --no-headless --interact --slow 1.0 --hold 30

# one extension at a time, matched by name or id
python3 src/bex_tracer/challenge/scripts/run_round.py --rounds 0 --ext dark --no-headless --interact --slow 1.0

# scored rounds, visible
python3 src/bex_tracer/challenge/scripts/run_round.py --rounds 2 --no-headless --settle 6
```

`--hold N` keeps the browser open N seconds at the end so you can poke at it — open devtools,
check `chrome://extensions`, inspect the DOM.

The gesture script is fixed and identical on every round (`_INTERACTIONS` in `_browser.py`):
accept cookies → type an email → type a password → type in the textarea → click the rich editor
→ scroll to the ad banner. It is deterministic on purpose: a gesture sequence that varied by
subset would leak which extensions are enabled. It is also a detection surface, not decoration —
some extensions only inject after a real user gesture on the field they care about.

| Script | Answers |
|---|---|
| `run_round.py --rounds 0` | *Do the extensions load, under their real store ids?* |
| `run_round.py --rounds N` | *Can the miner script tell which ones are loaded?* |

`run_round.py` prints ground truth beside the miner's prediction. **The API never may** — that
printout is the reason both are dev-only and neither is wired into `service.py`.

Everything under `volumes/` is gitignored; the `.crx` files and Chrome binaries are never
committed.

---

## Miner contract

The pool is published in **groups** (by extension category) - `GET /task`'s `groups` field maps
each group name to the extension **names** it owns. Names are the answer key; store ids are not
published. A submission is **one file per group**, named
`<group>.js`, each defining its own entrypoint. Every group is sent in a single `POST /score`:

For each group `g` that `GET /task` publishes, send `g.js` defining `window.detect_g`. The
grouping is generated from `extensions.yml` at runtime, so the set of files changes with the
pool — read it from `/task` rather than from any table, this one included.

```js
// vpn_proxy.js - returns this group's names and nothing else
window.detect_vpn_proxy = async function () {
  // ... probe the page ...
  return {
    "FoxyProxy": true,
    "Browsec VPN - Free VPN for Chrome": false,
    "Free VPN Proxy - 1VPN": false,
  };
};
```

### There are no stable extension ids

Extensions are loaded **unpacked with `key` stripped from their manifests**, so Chrome derives
each extension's id from the directory it was staged in - and every round stages into a fresh
directory. Consequences:

- The id an extension has in one round is gone in the next, and it is **never** its store id.
- `fetch("chrome-extension://<store-id>/<path>")` always fails. Every published id-keyed
  lookup table is dead weight here.
- Detection has to come from what the extension *does*: changed browser APIs and values,
  replaced native functions, injected nodes and stylesheets, post-gesture injection.

### The environment

- **Compose does not restrict egress.** Production deployments that require network isolation must
  enforce it outside this compose file. Miner JavaScript and extensions otherwise inherit the
  container's available network access.
- **The bait page is served as `https://baitpage.test:10443/_web`, not `127.0.0.1`.** Some
  extensions deliberately do nothing on localhost. Chrome maps the name to loopback and accepts
  the challenge's self-signed certificate, preserving a realistic secure context.

Take the names from `GET /task`, never from a doc - **the grouping is generated from
`extensions.yml` at runtime and changes when the pool changes.** Only the names `/task`
publishes are ever enabled; anything else you name is a guaranteed false positive.

Return a boolean for each extension name in *that group only* - the challenge merges every
file's answers before scoring, so a name from another group does not belong here. Missing keys are
treated as `false`. Every file runs each round, in parallel, in its own try/catch: a throw in one
group's file costs only that group's labels and the others still score normally. Scoring is
MCC over the whole pool, so a false positive costs real score and an honest `false` beats a
hopeful `true`.

See the reference baseline in [`examples/miner_commit/`](../examples/miner_commit/).

## Scope note

This container answers one question: *did the submitted JS correctly identify which extensions
were enabled?* — returning a single float in `[0,1]`.

Everything downstream of that number — similarity penalties, time decay, softmax normalisation
across miners, sybil collapse, on-chain weights — belongs to `redteam_core` / `scoring-api` in
`stack-redteam`, not here.

`GET /results` exposes the most recent run's statuses, timings, detected names, and aggregate score
without exposing ground truth. `POST /score` and `GET /results` require `X-API-Key`; `GET /task`
does not. Scoring is single-flight, so an overlapping request receives HTTP 429.
