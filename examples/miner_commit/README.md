# BEX Tracer Miner Submission

This directory is the miner submission template. Add your detector files,
configure the image name for your **private Docker Hub repository**, validate
the JavaScript, then build and push the image.

Run all commands below from `examples/miner_commit/`.

## Before you start

You need:

- Docker with Docker Compose;
- a Docker Hub account;
- a **private** Docker Hub repository;
- Node.js and npm for ESLint;
- the BEX Tracer task's current `groups` map.

The validator must be able to pull the private image. Configure its Docker Hub
pull credentials through the official miner submission workflow. Never put a
Docker Hub password or personal access token in this repository, a JavaScript
file, `compose.yml`, or the image.

## 1. Log in to Docker Hub

Create a private repository in your Docker Hub account, then authenticate the
Docker CLI:

```sh
docker login --username YOUR_DOCKERHUB_USERNAME
```

Enter a Docker Hub personal access token at the password prompt. Confirm the
command reports `Login Succeeded` before continuing.

## 2. Set the private repository and tag

Open `compose.yml` and replace the example `image:` value:

```yaml
services:
  miner-api:
    image: YOUR_DOCKERHUB_USERNAME/YOUR_PRIVATE_REPOSITORY:YOUR_TAG
```

Example:

```yaml
services:
  miner-api:
    image: alice/bex-tracer-submission:v1
```

Requirements:

- the Docker Hub repository must be private;
- the repository must belong to the account used by `docker login`;
- use a deliberate, immutable tag such as `v1` or a commit identifier;
- use this exact image name when submitting the miner commit.

Do not leave the template's
`redteamsubnet61/submission-exc-challenge:latest` image name in place.

## 3. Add the JavaScript submission

Put detector files in:

```text
src/commit/
```

The challenge publishes its required files through `GET /task`:

```json
{
  "groups": {
    "ad_blockers": ["uBlock Origin Lite", "AdGuard AdBlocker"],
    "developer_tools": ["..."]
  }
}
```

Create exactly one file per returned group:

```text
src/commit/ad_blockers.js
src/commit/developer_tools.js
...
```

Each `<group>.js` file defines `window.detect_<group>` and returns exact
extension names owned by that group:

```js
window.detect_ad_blockers = async function () {
  return {
    "uBlock Origin Lite": true,
    "AdGuard AdBlocker": false,
  };
};
```

Rules:

- derive group and extension names from the current task;
- return booleans keyed by exact extension display names;
- answer only for names owned by that file's group;
- keep every file at or below 750 lines and 262,144 UTF-8 bytes;
- do not include unrelated files in `src/commit/`.

`src/app.py` returns every `*.js` file found directly under `src/commit/`.
The challenge still requires exactly one file for every current group, so a
missing detector or an unrelated JavaScript file makes the submission invalid.
Keep this directory synchronized with the current task before publishing.

## 4. Validate with ESLint

The submission must pass the repository's `eslint.config.mjs` rules.

Install the local lint dependencies once:

```sh
npm init --yes
npm install --save-dev eslint @eslint/js globals
```

Run ESLint against all detector files using the checked-in configuration:

```sh
npx eslint --config eslint.config.mjs "src/commit/**/*.js"
```

No output and exit code 0 means the check passed. Fix every reported error
before building. ESLint can apply safe mechanical fixes:

```sh
npx eslint --config eslint.config.mjs "src/commit/**/*.js" --fix
npx eslint --config eslint.config.mjs "src/commit/**/*.js"
```

Review all automatic changes. A clean format does not prove the detectors are
correct.

## 5. Build and push the image

Build for the validator's `linux/amd64` platform. Docker Compose tags the
result with the private image name configured in `compose.yml`:

```sh
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose build miner-api
```

Optionally run the image locally:

```sh
docker compose up -d miner-api
curl --fail http://localhost:10002/health
docker compose down
```

Push the tagged image to the private Docker Hub repository:

```sh
docker compose push miner-api
```

Verify that Docker Hub received the tag:

```sh
docker buildx imagetools inspect \
  YOUR_DOCKERHUB_USERNAME/YOUR_PRIVATE_REPOSITORY:YOUR_TAG
```

Record the pushed image reference and registry digest. Submit them through the
official miner workflow together with the Docker Hub pull credential required
for the validator to access the private repository.

## Final checklist

- [ ] Logged in to the correct Docker Hub account.
- [ ] Created a private Docker Hub repository.
- [ ] Replaced `image:` in `compose.yml` with your repository and tag.
- [ ] Added one `src/commit/<group>.js` file per task group.
- [ ] Used the correct `window.detect_<group>` entrypoint in every file.
- [ ] Passed `eslint.config.mjs` with zero errors.
- [ ] Built the image for `linux/amd64`.
- [ ] Tested the container's `/health` endpoint.
- [ ] Pushed the exact configured tag.
- [ ] Recorded the registry digest.
- [ ] Configured private-repository pull access through the official workflow.

## Service contract

The image runs a FastAPI service on port `10002`:

- `GET /health` returns the service health;
- `POST /solve` accepts the current task and returns
  `{"commit_files": [...]}`;
- `GET /docs` exposes the local OpenAPI interface.

The `Dockerfile`, `src/app.py`, and `src/data_types.py` already implement
this service contract. Miners normally change only `compose.yml` and the
JavaScript files under `src/commit/`.
