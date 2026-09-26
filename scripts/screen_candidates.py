#!/usr/bin/env python3
"""Rank candidate extensions by how HARD they are to detect, from the manifest.

Pool curation used to mean: download 50, run 50 through a browser, keep 12.
Most of those failures were predictable without launching anything - the
manifest already says whether an extension hands over its own fingerprint.

Measured on the 2026-09-22 pool, 16 of 24 entries were detectable by reading a
downloaded `.crx` and nothing else:

  - 12 declare `content_scripts.css` on <all_urls>. Chrome injects that
    stylesheet on every page load whether the extension ever acts or not, so a
    miner builds an element matching any selector it ships and reads
    `getComputedStyle` back. LanguageTool alone injects 2,627 rules. No
    browser experimentation is needed - the selectors are in the file.
  - 7 declare `world: MAIN`, so the content script shares the page's JS world
    and its bundle variables are readable straight off `window`.

Neither is a flaw in the harness, and neither is cheating: the extension
really does put those things in the page. It is a pool composition problem,
and this script is how you avoid repeating it.

What survives this filter needs a browser and, ideally, a real interaction -
which is the difference between a pool that costs an attacker two hours and
one that costs a day.

    screen_candidates.py --ids ids.txt --out screened.json
    screen_candidates.py --lock src/bex_tracer/challenge/extensions.lock.yml

`--lock` re-scores what is already shipping, which is how the numbers above
were produced. Nothing here downloads more than the manifest and the css it
names, and nothing is written outside `--out`.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_extensions import CRX_URL, download, parse_crx3  # noqa: E402

BROAD_MATCHES = frozenset(
    {"<all_urls>", "*://*/*", "http://*/*", "https://*/*"}
)

# Ordered worst-to-best. A candidate is scored by the cheapest route into it,
# because that is what an attacker will actually use.
TIERS = (
    ("manifest_css", 0, "ships content_scripts.css on every page"),
    ("main_world", 1, "runs a MAIN-world script; globals readable"),
    ("dom_only", 2, "injects into the page; needs a browser to find"),
    ("interaction", 3, "nothing until the page is interacted with"),
    ("inert", -1, "no content script on this page at all - UNANSWERABLE"),
)


def _broad(matches: list[str] | None) -> bool:
    return any(m in BROAD_MATCHES for m in matches or [])


def manifest_of(eid: str, cft_version: str) -> tuple[dict[str, Any], zipfile.ZipFile]:
    """The manifest plus the open archive, so css can be read without unpacking."""
    crx = download(CRX_URL.format(ver=cft_version, eid=eid))
    _declared_id, _pubkey, zip_bytes = parse_crx3(crx)
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    return json.loads(zf.read("manifest.json")), zf


def screen(eid: str, name: str, cft_version: str) -> dict[str, Any]:
    """One candidate, scored by its cheapest detection route."""
    try:
        manifest, zf = manifest_of(eid, cft_version)
    except Exception as err:  # noqa: BLE001 - one bad id must not stop a sweep
        return {"id": eid, "name": name, "tier": "error", "why": str(err)[:80]}

    css_files: list[str] = []
    main_world = False
    runs_js = False
    for cs in manifest.get("content_scripts") or []:
        if not _broad(cs.get("matches")):
            continue
        css_files += cs.get("css") or []
        main_world = main_world or cs.get("world") == "MAIN"
        runs_js = runs_js or bool(cs.get("js"))

    # Rule count is the useful number, not file count: a one-file stylesheet
    # with 2,600 rules offers 2,600 selectors to probe.
    rules = 0
    for f in css_files:
        try:
            rules += zf.read(f).decode("utf-8", "ignore").count("{")
        except Exception:  # noqa: BLE001 - a missing css file is not fatal
            continue
    zf.close()

    if css_files:
        tier = "manifest_css"
    elif main_world:
        tier = "main_world"
    elif runs_js:
        tier = "dom_only"
    else:
        tier = "inert"

    return {
        "id": eid,
        "name": name or manifest.get("name", eid),
        "version": manifest.get("version"),
        "tier": tier,
        "css_rules": rules,
        "main_world": main_world,
        "runs_js": runs_js,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", type=Path, help="score an existing extensions.lock.yml")
    ap.add_argument("--ids", type=Path, help="file of `id[ name]` lines to screen")
    ap.add_argument("--out", type=Path, help="write the full result as JSON")
    ap.add_argument("--cft", default="152.0.7977.54", help="Chrome for Testing version")
    args = ap.parse_args()

    candidates: list[tuple[str, str]] = []
    if args.lock:
        import yaml

        for name, e in (yaml.safe_load(args.lock.read_text()) or {}).items():
            candidates.append((e["id"], name))
    if args.ids:
        for line in args.ids.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            eid, _, nm = line.partition(" ")
            candidates.append((eid, nm.strip()))
    if not candidates:
        ap.error("give --pool or --ids")

    rows = []
    for i, (eid, nm) in enumerate(candidates, 1):
        row = screen(eid, nm, args.cft)
        rows.append(row)
        print(f"  [{i:>3}/{len(candidates)}] {row['name'][:30]:<32} {row['tier']}", flush=True)

    order = {t[0]: t[1] for t in TIERS}
    rows.sort(key=lambda r: (order.get(r["tier"], 9), -r.get("css_rules", 0)))

    print("\n" + "=" * 66)
    print(f"{'tier':<14}{'n':>4}  what it means")
    print("-" * 66)
    for tier, _, why in TIERS:
        n = sum(1 for r in rows if r["tier"] == tier)
        if n:
            print(f"{tier:<14}{n:>4}  {why}")
    err = sum(1 for r in rows if r["tier"] == "error")
    if err:
        print(f"{'error':<14}{err:>4}  could not download or parse")

    good = [r for r in rows if r["tier"] in ("dom_only", "interaction")]
    print(f"\nWORTH AUDITING ({len(good)}): these need a browser to crack")
    for r in good:
        print(f"  {r['name'][:34]:<36} {r['id']}")
    print("\nNote `interaction` is never reported here - a manifest cannot say")
    print("whether a signal needs a gesture. Audit the `dom_only` set to find out;")
    print("that is the remaining browser work, and it is now a much shorter list.")

    if args.out:
        args.out.write_text(json.dumps(rows, indent=1))
        print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
