# Extension Classification Challenge — Docs

A RedTeam Subnet challenge where miners submit JavaScript that fingerprints **which browser
extensions are active** in a Chrome session.

The harness launches Chrome for Testing with a random subset of extensions drawn from a published
pool, loads a static bait page, runs the miner's script from page context, and scores how
accurately it identified what was installed.

## Contents

| Document | What's in it |
|---|---|
| [`REFERENCE.md`](./REFERENCE.md) | **Self-contained technical reference** — verified facts, working CRX3 parser, Chrome flags, Selenium API, current repo state. No web search needed. |
| [`design.md`](./design.md) | The reasoning: architecture, scoring rationale, extension pool, container spec, anti-cheat, risks |
| [`pipeline.html`](./pipeline.html) | Pool-to-score pipeline diagram — open in a browser |
| [`architecture.excalidraw`](./architecture.excalidraw) | Flow diagram — open at [excalidraw.com](https://excalidraw.com) |
| [`release-notes.md`](./release-notes.md) | Release notes |

## At a glance

| | |
|---|---|
| Threat model | Page-context JS, injected post-load via `execute_async_script` |
| Browser | Chrome for Testing — headless in prod, headful under Xvfb for dev |
| Extension pool | 21 Chrome Web Store extensions, IDs published to miners (8 more sit in `rejected:`) |
| Enabled per round | Fixed `k = 5` (`k` in config), random subset, never revealed |
| Miner output | 7 files, one per group, each `{extensionId: true\|false}` for that group's IDs |
| Metric | MCC over all N binary decisions, `max(0, mcc)` → `[0,1]` |

## Running it locally

Selenium lives in `api/endpoints/challenge/_browser.py`. `challenge/scripts/run_round.py` drives it
end-to-end without Docker or the API — this is the loop to use while curating the pool.

```sh
pip install selenium psutil pyyaml
pip install -r src/exc_challenge/challenge/requirements.txt

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
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 0    # whole pool, headless

# 4. Scored rounds (needs a miner script; not required to check loading)
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 4
```

### Watching it work

`--no-headless` opens a real Chrome window; `--interact` makes Selenium click and type its way
through the page; `--slow` paces the steps so you can follow along.

```sh
# the full show: visible window, Selenium driving, one step per second
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 0 --no-headless --interact --slow 1.0 --hold 30

# one extension at a time, matched by name or id
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 0 --ext dark --no-headless --interact --slow 1.0

# scored rounds, visible
python3 src/exc_challenge/challenge/scripts/run_round.py --rounds 2 --no-headless --settle 6
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
each group name to the extension ids it owns. A submission is **one file per group**, named
`<group>.js`, each defining its own entrypoint. All seven are sent in a single `POST /score`:

| group | n | file | entrypoint |
|---|---|---|---|
| `blockers` | 3 | `blockers.js` | `window.detect_blockers` |
| `password_managers` | 4 | `password_managers.js` | `window.detect_password_managers` |
| `shopping` | 3 | `shopping.js` | `window.detect_shopping` |
| `writing` | 3 | `writing.js` | `window.detect_writing` |
| `appearance_media` | 4 | `appearance_media.js` | `window.detect_appearance_media` |
| `translate` | 2 | `translate.js` | `window.detect_translate` |
| `productivity` | 2 | `productivity.js` | `window.detect_productivity` |

```js
// blockers.js - returns this group's ids and nothing else
window.detect_blockers = async function () {
  // ... probe the page ...
  return {
    "cfhdojbkjhnklbpkdaibdccddilifddb": true,   // Adblock Plus
    "pkehgijcmpdhfbdbbnkijodmdjhbjlgp": false,  // Privacy Badger
    "bkdgflcldnnnapblkhphbgpggdiikppg": false,  // DuckDuckGo
  };
};
```

Take the ids from `GET /task`, never from a doc - **the grouping is generated from
`extensions.yml` at runtime and changes when the pool changes.** Ids in `extensions.yml`'s
`rejected:` block are never enabled in any round, so claiming one is a guaranteed false positive.

Return a boolean for each extension ID in *that group only* - the challenge merges all seven
files' answers before scoring, so an id from another group does not belong here. Missing keys are
treated as `false`. All seven run every round, in parallel, each in its own try/catch: a throw in
one group's file costs only that group's labels and the others still score normally. Scoring is
MCC over the whole pool, so a false positive costs real score and an honest `false` beats a
hopeful `true`.

See [`design.md`](./design.md) for the full contract and the reference baseline in
[`examples/miner_commit/`](../examples/miner_commit/).

## Scope note

This container answers one question: *did the submitted JS correctly identify which extensions
were enabled?* — returning a single float in `[0,1]`.

Everything downstream of that number — similarity penalties, time decay, softmax normalisation
across miners, sybil collapse, on-chain weights — belongs to `redteam_core` / `scoring-api` in
`stack-redteam`, not here.
