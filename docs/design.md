# Extension Classification Challenge — As-built design

This document describes the implementation in this repository. Runtime values may be overridden by
environment or config, so `GET /task`, `ChallengeConfig`, and `extensions.yml` remain authoritative.

## Purpose

Miners submit page-context JavaScript that identifies which browser extensions are active in a real
Chrome session. The challenge creates a fresh browser profile for every round, loads a secret subset
of the published pool, opens a static bait page, performs deterministic user gestures, runs the
submission, and scores its boolean predictions.

The browser driver is in this repository. It uses Selenium and Chrome for Testing; no external
bot-runner participates in scoring.

## Current production defaults

| Setting | Value |
|---|---:|
| Published pool | 89 extension names |
| Groups | 8 |
| Rounds per `POST /score` | 6 |
| Enabled per round | 12, fixed |
| Coverage bias | 2.0 |
| Page settle | 6 seconds |
| Post-gesture settle | 1.5 seconds |
| Script budget | 10 seconds |
| Maximum submission | 750 lines and 262,144 bytes per file |
| Browser | Chrome for Testing 152.0.7977.54, `linux/amd64` |
| Metric | Mean of per-round `max(0, MCC)` |

Production values live in
`src/bex_tracer/challenge/api/core/configs/_challenge.py`. The checked-in config template mirrors
them. The pool and group membership live in `src/bex_tracer/challenge/extensions.yml`.

## Public contract

### `GET /task`

Returns:

- `random_val`, used by the wider subnet protocol to prevent caching;
- `extension_names`, the full pool by display name;
- `groups`, a map from group name to the names owned by that group.

Store IDs are not returned. Extension names are the scoring labels.

### Miner output

The miner submits exactly one JavaScript file for every published group:

```text
ad_blockers.js
capture_recording.js
developer_tools.js
identity_security.js
media_video.js
network_vpn.js
save_research.js
tabs_workflow.js
```

Each `<group>.js` defines `window.detect_<group>` and returns booleans only for names belonging to
that group:

```js
window.detect_ad_blockers = async function () {
  return {
    "uBlock Origin Lite": true,
    "AdGuard AdBlocker": false,
  };
};
```

The exact files and labels must always be derived from `GET /task`, not copied from this document.
Missing labels count as `false`; labels outside the pool are discarded. A file may not answer for a
different group. Group functions run concurrently inside one browser round, each in its own
`try/catch`, so one broken group does not erase successful answers from the others.

`POST /score` accepts the subnet-wide `{miner_input, miner_output}` envelope. The server deliberately
ignores the posted `miner_input` while scoring and reloads its own pool, preventing a caller from
choosing an easier label set. It returns one float in `[0, 1]`.

`GET /results` returns the most recent run report: aggregate score, round counts, duration, status,
and names detected by the miner. It never returns the enabled subset or detailed error text.

`POST /score` and `GET /results` require `X-API-Key`; `GET /task` is public. A non-blocking lock allows
one scoring run at a time; overlapping score requests receive HTTP 429.

## Identity model

Chrome Web Store IDs are build inputs, not runtime labels. `fetch_extensions.py` downloads the CRX,
checks its pinned version and SHA-256 digest, and unpacks it without adding a manifest `key`.

Before every round, enabled extension directories are copied to a fresh, secret scratch path. Chrome
derives unpacked-extension IDs from those paths, so each enabled extension receives a different ID
every round. The nonce used in the path is generated server-side and is not exposed in responses,
headers, logs above DEBUG, or the bait page.

Consequences:

- a Chrome Web Store ID cannot be probed directly at runtime;
- an ID learned in one round is useless in another;
- miners must identify page-visible behavior rather than rely on a static ID/resource table.

The server verifies that Chrome loaded the expected derived IDs before opening the bait page. A
missing or extra challenge extension is infrastructure failure, not a miner mistake.

## Round lifecycle

For each of six sequential rounds:

1. `build_round_schedule()` selects 12 names using `secrets` and weights less-used extensions more
   heavily (`coverage_bias=2.0`). The subset remains only in server memory.
2. Submission files, staged once for the whole run, are served by the bait page through ordinary
   `<script src>` tags.
3. The selected unpacked extensions are copied to `/run/exc/round-<nonce>-<index>/ext/`.
4. Chrome starts with a fresh profile and `--load-extension=<paths>`.
5. The server verifies loaded IDs through `chrome://extensions-internals/` (falling back to profile
   preferences when needed).
6. Chrome opens `https://baitpage.test:10443/_web`. A host-resolver rule maps the name to loopback;
   the private HTTPS listener uses a self-signed certificate accepted only for this browser run.
7. The page settles for 6 seconds.
8. Selenium performs the same six gestures: accept cookies, type in email/password/notes fields,
   click the rich editor, and scroll to the ad banner.
9. After 1.5 seconds, the wrapper calls all `window.detect_<group>` functions with a 10-second
   budget and normalizes the result.
10. Chrome processes and the scratch directory are removed.

Rounds are intentionally sequential. Concurrent Chrome instances would compete for memory and CPU,
making timing-dependent labels unreliable.

## Bait page and observable signals

The bait page contains no ground truth. It provides stable detection surfaces:

- ad-shaped elements and tracker-like resources;
- email, password, textarea, and contenteditable fields;
- cookie UI, links, images, canvas, and media-related APIs;
- deterministic user gestures for extensions that activate only after interaction.

Useful detection signals include DOM or style changes, injected shadow roots, wrapped browser APIs,
request blocking, post-gesture UI, and timing. Some pool entries may have no observable footprint
under this page; that is part of the competitive challenge and affects every miner equally.

Chrome resolves `baitpage.test` directly to loopback, so the bait page needs no external DNS.
Compose does not restrict runtime egress. Deployments requiring network isolation must enforce it
outside this compose file; miner JavaScript and extensions otherwise inherit container networking.

## Scoring

For every round, the challenge compares all 89 labels, not only the 12 enabled labels:

```text
MCC = (TP × TN - FP × FN)
      / sqrt((TP + FP)(TP + FN)(TN + FP)(TN + FN))

round_score = max(0, MCC)
run_score   = mean(scored round_score values)
```

If the MCC denominator is zero, the round score is `0.0`. Thus all-true and all-false submissions
carry no information and score zero. Negative correlation is clamped to zero. A miner failure stays
in the denominator as a zero; an infrastructure failure is excluded.

If more than 20% of rounds lose the browser, the whole request fails instead of returning a score
based on too little evidence. With six rounds, two infrastructure failures cross that threshold.

## Failure boundaries

| Failure | Result |
|---|---|
| One group throws, hangs, is missing, or returns junk | That group's labels become `false`; other groups still count |
| All groups fail for miner-caused reasons | Round scores `0.0`; run continues |
| Browser launch, staging, navigation, or renderer fails | Round is `INFRA_FAILED` and excluded |
| More than 20% infrastructure failures | `POST /score` fails |
| Invalid pool or `k >= pool size` | Request fails before useful scoring |
| Concurrent scoring request | HTTP 429 |

## Build and runtime boundaries

At image build time:

1. Chrome and chromedriver archives are downloaded and SHA-256 verified.
2. `fetch_extensions.py` downloads each pinned CRX and verifies version/digest.
3. Extensions are unpacked to `/opt/extensions/<store-id>/` without manifest keys.

At runtime, only selected directories are copied to the executable tmpfs at `/run/exc`. The
container uses a 2 GiB shared-memory segment, an 8 GiB memory limit, a 4,096 PID limit,
`no-new-privileges`, and a reduced capability set.

Everything after this container's float—similarity checks, time decay, miner normalization, sybil
handling, and on-chain weights—belongs to `redteam_core` / `scoring-api`, not this repository.

## Source map

| Concern | Source |
|---|---|
| Pool and pins | `src/bex_tracer/challenge/extensions.yml` |
| Config defaults | `src/bex_tracer/challenge/api/core/configs/_challenge.py` |
| Task/submission schemas | `src/bex_tracer/challenge/api/endpoints/challenge/schemas.py` |
| HTTP auth and single-flight lock | `src/bex_tracer/challenge/api/endpoints/challenge/router.py` |
| Orchestration | `src/bex_tracer/challenge/api/endpoints/challenge/service.py` |
| Scheduling and MCC | `src/bex_tracer/challenge/api/endpoints/challenge/_payload_manager.py` |
| Browser lifecycle | `src/bex_tracer/challenge/api/endpoints/challenge/_browser.py` |
| Pool validation | `src/bex_tracer/challenge/api/endpoints/challenge/_pool.py` |
| Submission staging | `src/bex_tracer/challenge/api/endpoints/challenge/utils.py` |
| CRX acquisition | `scripts/fetch_extensions.py` |
| Local round driver | `src/bex_tracer/challenge/scripts/run_round.py` |

## Verification

Run the focused suite after changing the contract, schedule, browser, or pool:

```sh
pytest -q \
  tests/test_config_defaults.py \
  tests/test_pool_validation.py \
  tests/test_payload_manager.py \
  tests/test_grouped_submissions.py \
  tests/test_detection_staging.py \
  tests/test_browser.py \
  tests/test_challenge_score.py
```

Use `run_round.py --rounds 0` to verify that the installed pool loads, then run scored rounds with a
miner submission. Add `--no-headless --interact --slow 1.0` when visually inspecting extension
behavior.
