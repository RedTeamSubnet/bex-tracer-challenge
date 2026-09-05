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

| file | entrypoint | owns |
|---|---|---|
| `blockers.js` | `window.detect_blockers` | 4 ids |
| `password_managers.js` | `window.detect_password_managers` | 5 ids |
| `shopping.js` | `window.detect_shopping` | 3 ids |
| `writing.js` | `window.detect_writing` | 3 ids |
| `appearance_media.js` | `window.detect_appearance_media` | 5 ids |
| `translate.js` | `window.detect_translate` | 2 ids |
| `productivity.js` | `window.detect_productivity` | 5 ids |

`src/commit/` holds a runnable stub for each. They return all-`false` - a valid submission that
scores 0. Finding the signals is the challenge.

**Get the group names and ids from `GET /task`**, not from this table. The grouping is generated
from the challenge's pool file at runtime and changes when the pool rotates:

```json
{
  "extension_ids": ["cfhdojbkjhnklbpkdaibdccddilifddb", "..."],
  "groups": { "blockers": ["cfhdojbkjhnklbpkdaibdccddilifddb", "..."], "...": [] }
}
```

Each entrypoint may be `async`, and returns ids for **its own group only**:

```js
window.detect_blockers = async function () {
  return { "cfhdojbkjhnklbpkdaibdccddilifddb": true, /* ... */ };
};
```

### Rules that decide your score

- **All seven files are required.** A missing or unexpected filename is rejected outright.
- **≤ 500 lines per file.**
- **All seven run every round**, in parallel, each in its own `try`/`catch`. A throw costs only
  that group's ids; the rest still score. The round is only lost if all seven fail.
- **A missing key counts as `false`**, as does a throw.
- **Scoring is MCC over the whole pool.** A false positive costs real score, so an honest `false`
  beats a hopeful `true`. Answering all-`true` or all-`false` scores 0.
- **You are not told how many are enabled**, or which.
- Your script runs under a fixed per-round time budget; overrunning it loses the round.

Where to look: `web_accessible_resources` probes (`fetch("chrome-extension://<id>/<path>")`),
injected stylesheets and DOM footprint, blocked network requests, and behaviour that only appears
after a user gesture. The stubs in `src/commit/` carry more detail per group.

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
