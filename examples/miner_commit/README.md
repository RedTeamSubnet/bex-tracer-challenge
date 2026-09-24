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
groups = { "ad_blockers": [...], "vpn_proxy": [...], ... }   from GET /task
            |
            +--> ad_blockers.js  defining window.detect_ad_blockers
            +--> vpn_proxy.js    defining window.detect_vpn_proxy
                 ... one file per group, all of them, in one response
```

**The group names come from `GET /task` and nowhere else.** They are generated from the
challenge's pool at runtime and change whenever the pool does, so anything hardcoded - including
in this file - goes stale. `src/app.py` builds its file list from `miner_input.groups` for
exactly that reason; a group with no detector gets an empty stub so the submission stays valid.

Put your per-group files in `src/commit/`. Any group without a file gets an empty stub that
returns `false` for every name - a valid submission that scores 0, so you can start from nothing.

```json
{
  "extension_names": ["FoxyProxy", "..."],
  "groups": { "vpn_proxy": ["FoxyProxy", "Free VPN Proxy - 1VPN", "..."], "...": [] }
}
```

Each entrypoint may be `async`, and returns names for **its own group only**:

```js
window.detect_vpn_proxy = async function () {
  return { "FoxyProxy": true, /* ... */ };
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
- **≤ 750 lines per file.**
- **Every file runs every round**, in parallel, each in its own `try`/`catch`. A throw costs
  only that group's names; the rest still score. The round is lost only if they all fail.
- **A missing key counts as `false`**, as does a throw.
- **Scoring is MCC over the whole pool.** A false positive costs real score, so an honest `false`
  beats a hopeful `true`. Answering all-`true` or all-`false` scores 0.
- **You are not told how many are enabled**, or which.
- Your script runs under a fixed per-round time budget; overrunning it loses the round.

### The environment

- **No internet.** The challenge container has no DNS, so any request to another host fails -
  for every extension and for your code alike. Only the bait page's own server answers.
- **The bait page is served under a hostname, not `127.0.0.1`,** and is a secure context, so
  secure-only APIs such as `crypto.subtle` are available.

### Where to look

Not at ids - see above; that route is closed. What is left is what the extension *does*. Most of
the pool changes **browser APIs** rather than the page, so DOM diffing alone will not get you far:

- **Changed values** - what the browser reports about itself can differ from a clean browser.
- **Replaced functions** - a native function an extension has wrapped is no longer the original,
  even when it tries to look like one.
- **Changed behaviour** - calling the same API twice does not always give the same answer.
- **Page footprint** - injected nodes, stylesheets, attributes stamped on `<html>` or `<body>`.
- **Post-gesture behaviour** - some extensions do nothing until a real interaction.

### What is allowed

Anything a normal web page can do. Calling an API repeatedly, reading a function's source,
inspecting prototypes and descriptors, triggering an error on purpose, dispatching events - all
fair: detecting what an extension did *is* the challenge. Not allowed: reading or inferring the
enabled set from anywhere but the page, disabling or undoing an extension, tampering with the
harness (timers, wrapper, scoring, staged files), and answering `true` without evidence.

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
