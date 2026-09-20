# Miner Commit - Extension Classification

This is a miner commit API example for Extension Classification.

## ✨ Features

- Miner commit
- Health check endpoint
- FastAPI
- Web service

---

## 📋 What you submit

The challenge enables a random subset of Chrome extensions, loads a bait page, and runs your
JavaScript in that page. You return one boolean per extension: was it enabled?

The pool is split into **groups** by category. You submit **one file per group** - all of them,
in a single response - named `<group>.js`, each defining `window.detect_<group>`:

```
groups = { "blockers": [...], "developer": [...], ... }   from GET /task
            |
            +--> blockers.js   defining window.detect_blockers
            +--> developer.js  defining window.detect_developer
                 ... one file per group, all of them, in one response
```

**The group names come from `GET /task` and nowhere else.** They are generated from the
challenge's pool at runtime and change whenever the pool does, so anything hardcoded - including
in this file - goes stale. `src/app.py` builds its file list from `miner_input.groups` for
exactly that reason; a group with no detector gets an empty stub so the submission stays valid.

`src/commit/` holds a runnable stub per group. They return all-`false`: a valid submission that
scores 0. Finding the signals is the challenge.

```json
{
  "extension_names": ["Adblock Plus", "..."],
  "groups": { "blockers": ["Adblock Plus", "Privacy Badger", "DuckDuckGo"], "...": [] }
}
```

Each entrypoint may be `async`, and returns names for **its own group only**:

```js
window.detect_blockers = async function () {
  return { "Adblock Plus": true, /* ... */ };
};
```

### There are no extension ids to probe

Extensions are loaded unpacked with `key` stripped from their manifests, so Chrome derives each
id from the directory it was staged in - and every round uses a fresh directory. The id an
extension has this round is gone the next one, and it is never its Chrome Web Store id. A
hardcoded `chrome-extension://<store-id>/...` fetch always fails, and published id-keyed lookup
tables are worthless here. Detect what the extension *does* to the page instead.

### Rules that decide your score

- **Every group needs a file.** A missing or unexpected filename is rejected outright -
  the whole submission, not just that group.
- **≤ 500 lines per file.**
- **Every file runs every round**, in parallel, each in its own `try`/`catch`. A throw costs
  only that group's names; the rest still score. The round is lost only if they all fail.
- **A missing key counts as `false`**, as does a throw.
- **Scoring is MCC over the whole pool.** A false positive costs real score, so an honest `false`
  beats a hopeful `true`. Answering all-`true` or all-`false` scores 0.
- **You are not told how many are enabled**, or which.
- Your script runs under a fixed per-round time budget; overrunning it loses the round.

### Where to look

Not at ids - see above; that route is closed. What is left is what the extension *does*:

- **Page footprint** - injected nodes, shadow roots, stylesheets, changed computed styles,
  attributes stamped on `<html>` or `<body>`.
- **Blocked requests** - an extension that cancels a request leaves a different `performance`
  resource timeline than one that does not. Use a control request, or a page where everything is
  blocked looks the same as a page with no blocker at all.
- **Post-gesture behaviour** - some extensions inject nothing until a real interaction with the
  element they care about.
- **Tampered natives** - `fetch`, `XMLHttpRequest`, `addEventListener` and friends are not always
  the originals once a content script has run.
- **Timing** - an extension can run a content script on every page and still change nothing you
  can see. It is not invisible: it costs time. A pool deliberately contains extensions that only
  show up this way, so DOM diffing alone will not get you a high score.

The stubs in `src/commit/` carry more detail per group.

---

## 🛠 Installation

### 1. 🚧 Prerequisites

- Install **Python (>= v3.10)** and **pip (>= 23)**:
    - **[RECOMMENDED] [Miniconda (v3)](https://www.anaconda.com/docs/getting-started/miniconda/install)**
    - *[arm64/aarch64] [Miniforge (v3)](https://github.com/conda-forge/miniforge)*
    - *[Python virtual environment] [venv](https://docs.python.org/3/library/venv.html)*

[OPTIONAL] For **DEVELOPMENT** environment:

- Install [**git**](https://git-scm.com/downloads)
- Setup an [**SSH key**](https://docs.github.com/en/github/authenticating-to-github/connecting-to-github-with-ssh)

### 2. 📦 Install dependencies

```sh
pip install -r ./requirements.txt
```

### 3. 🏁 Start the server

```sh
cd src
uvicorn app:app --host="0.0.0.0" --port=10002 --no-access-log --no-server-header --proxy-headers --forwarded-allow-ips="*"

# For DEVELOPMENT:
uvicorn app:app --host="0.0.0.0" --port=10002 --no-access-log --no-server-header --proxy-headers --forwarded-allow-ips="*" --reload
```

### 4. ✅ Check server is running

Check with CLI (curl):

```sh
# Send a ping request with 'curl' to API server:
curl -s http://localhost:10002/ping
```

Check with web browser:

- Health check: <http://localhost:10002/health>
- Swagger: <http://localhost:10002/docs>
- Redoc: <http://localhost:10002/redoc>
- OpenAPI JSON: <http://localhost:10002/openapi.json>

---

## 🏗️ Build Docker Image

To build the docker image, run the following command:

```sh
docker build -t redteamsubnet61/submission-exc-challenge:0.0.1 .

# For MacOS (Apple Silicon) to build AMD64:
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 -t redteamsubnet61/submission-exc-challenge:0.0.1 .
```
