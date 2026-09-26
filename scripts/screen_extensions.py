#!/usr/bin/env python3
"""Report the manifest facts for every extension in the pool. CURATION AID.

These facts are deliberately NOT stored in `extensions.yml`: they live in each
extension's manifest, and the store re-publishes often enough that a copy in
yaml would be quietly wrong within days. Run this when you need them.

    python3 scripts/screen_extensions.py
    python3 scripts/screen_extensions.py --json

Columns:
    mv     manifest_version. Anything but 3 cannot load in Chrome 152.
    WAR    web_accessible_resources paths, and whether they are dynamic.
           dynamic=yes means chrome-extension:// probing is dead for it.
    CS     content scripts, and whether any runs on <all_urls>. `all=no`
           means it is site-scoped and a generic bait page will not trigger it.
    DNR    declarativeNetRequest rulesets enabled / declared.
"""

import argparse
import hashlib
import io
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_extensions import (  # noqa: E402
    CRX_URL,
    _DEFAULT_CFT_VERSION,
    download,
    parse_crx3,
)

_ALL_URLS = ("<all_urls>", "*://*/*", "http://*/*", "https://*/*")


def _read_manifest(zip_bytes: bytes) -> dict:
    archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))

    name = manifest.get("name", "")
    if name.startswith("__MSG_"):
        # Localised name; the literal string lives in the default locale.
        locale = manifest.get("default_locale", "en")
        try:
            messages = json.loads(
                archive.read(f"_locales/{locale}/messages.json").decode("utf-8-sig")
            )
            manifest["name"] = messages.get(name[6:-2], {}).get("message", name)
        except (KeyError, ValueError):
            pass
    return manifest


def _war(manifest: dict) -> tuple[int, bool | None]:
    """(path count, all-dynamic?). None when there are no WAR entries."""
    blocks = manifest.get("web_accessible_resources") or []
    if not blocks:
        return 0, None
    if isinstance(blocks[0], str):  # MV2 shape: a flat list of paths
        return len(blocks), False
    paths = sum(len(b.get("resources", [])) for b in blocks)
    return paths, all(bool(b.get("use_dynamic_url")) for b in blocks)


def _content_scripts(manifest: dict) -> tuple[int, bool]:
    blocks = manifest.get("content_scripts") or []
    on_all_urls = any(
        match in _ALL_URLS for b in blocks for match in b.get("matches", [])
    )
    return len(blocks), on_all_urls


def screen(entry: dict, *, cft_version: str) -> dict:
    ext_id = entry["id"]
    label = entry.get("name", ext_id)
    try:
        crx = download(CRX_URL.format(ver=cft_version, eid=ext_id), timeout=90)
        derived, _spki, zip_bytes = parse_crx3(crx)
        manifest = _read_manifest(zip_bytes)
    except Exception as err:  # one failure must not stop the sweep
        return {"pinned_name": label, "id": ext_id, "error": str(err)}

    war_paths, war_dynamic = _war(manifest)
    n_cs, cs_all_urls = _content_scripts(manifest)
    rulesets = manifest.get("declarative_net_request", {}).get("rule_resources") or []
    return {
        "pinned_name": label,
        "id": ext_id,
        # A wrong id cannot reach here: parse_crx3 derives it from the signing key.
        "id_matches": derived == ext_id,
        "name": manifest.get("name", ""),
        "version": manifest.get("version"),
        "manifest_version": manifest.get("manifest_version"),
        "sha256": hashlib.sha256(crx).hexdigest(),
        "sha256_matches": entry.get("sha256")
        in (None, hashlib.sha256(crx).hexdigest()),
        "war_paths": war_paths,
        "war_dynamic": war_dynamic,
        "content_scripts": n_cs,
        "cs_all_urls": cs_all_urls,
        "dnr_enabled": sum(1 for r in rulesets if r.get("enabled")),
        "dnr_total": len(rulesets),
        "error": None,
    }


def format_row(r: dict) -> str:
    if r["error"]:
        return f"  {r['pinned_name']:26} !! {r['error'][:70]}"
    dynamic = {True: "dyn", False: "static", None: "-"}[r["war_dynamic"]]
    flags = "".join(
        [
            "" if r["id_matches"] else " ID-MISMATCH",
            "" if r["manifest_version"] == 3 else f" MV{r['manifest_version']}",
            "" if r["sha256_matches"] else " SHA-DRIFT",
        ]
    )
    return (
        f"  {r['pinned_name']:26} {str(r['version']):>12}"
        f"  {r['war_paths']:>3} {dynamic:<6}"
        f"  {r['content_scripts']:>3} {'all' if r['cs_all_urls'] else 'site':<4}"
        f"  {r['dnr_enabled']}/{r['dnr_total']}{flags}"
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lock", default=str(repo_root / "src/bex_tracer/challenge/extensions.lock.yml"))
    ap.add_argument("--json", action="store_true", help="emit raw json instead")
    args = ap.parse_args()

    lock = yaml.safe_load(Path(args.lock).read_text()) or {}
    cft_version = _DEFAULT_CFT_VERSION
    pool = [{"name": name, **entry} for name, entry in lock.items()]
    if not pool:
        print("ERROR: empty pool", file=sys.stderr)
        return 1

    with ThreadPoolExecutor(max_workers=6) as workers:
        results = list(workers.map(lambda e: screen(e, cft_version=cft_version), pool))

    if args.json:
        json.dump(results, sys.stdout, indent=1)
        return 0

    print(f"  {'extension':26} {'version':>12}  WAR         CS       DNR")
    print("  " + "-" * 74)
    for r in results:
        print(format_row(r))

    problems = [r for r in results if r["error"] or not r.get("id_matches", True)]
    stale = [r for r in results if not r["error"] and not r["sha256_matches"]]
    mv2 = [r for r in results if not r["error"] and r["manifest_version"] != 3]
    print(
        f"\n  {len(results)} screened, {len(problems)} unreachable, "
        f"{len(stale)} sha-drifted, {len(mv2)} not manifest v3"
    )
    return 1 if (problems or mv2) else 0


if __name__ == "__main__":
    raise SystemExit(main())
