# Extension Classification Challenge — Docs

A RedTeam Subnet challenge where miners submit JavaScript that fingerprints **which browser
extensions are active** in a Chrome session.

The harness launches Chrome for Testing with a random subset of extensions drawn from a published
pool, loads a static bait page, runs the miner's script from page context, and scores how
accurately it identified what was installed.

## Contents

| Document | What's in it |
|---|---|
| [`design.md`](./design.md) | Design + build plan: architecture, scoring, extension pool, container spec, anti-cheat, risks, verification |
| [`architecture.excalidraw`](./architecture.excalidraw) | Flow diagram — open at [excalidraw.com](https://excalidraw.com) |
| [`release-notes.md`](./release-notes.md) | Release notes |

## At a glance

| | |
|---|---|
| Threat model | Page-context JS, injected post-load via `execute_async_script` |
| Browser | Chrome for Testing — headless in prod, headful under Xvfb for dev |
| Extension pool | ~30 Chrome Web Store extensions, IDs published to miners |
| Enabled per trial | Random `k ∈ [3,8]`, subset never revealed |
| Miner output | `{extensionId: true\|false}` for every ID in the pool |
| Metric | MCC over all N binary decisions, `max(0, mcc)` → `[0,1]` |

## Miner contract

`solution.js` defines:

```js
window.detect_extensions = async function () {
  // ... probe the page ...
  return { "cjpalhdlnbpafiamejdnhcphjbkeiagm": true, /* ... */ };
};
```

Return a boolean for each extension ID in the published pool. Missing keys are treated as
`false`. See [`design.md`](./design.md) for the full contract and the reference baseline in
`examples/miner_commit/`.

## Scope note

This container answers one question: *did the submitted JS correctly identify which extensions
were enabled?* — returning a single float in `[0,1]`.

Everything downstream of that number — similarity penalties, time decay, softmax normalisation
across miners, sybil collapse, on-chain weights — belongs to `redteam_core` / `scoring-api` in
`stack-redteam`, not here.
