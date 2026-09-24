#!/usr/bin/env python3
"""Launch Chrome with a subset of the pool loaded, and report what a miner sees.

Runs the same way in the container and from a repo checkout - the only
difference is where the binaries live, which is detected rather than configured.

    run_round.py                                    one round, all-false stub
    run_round.py --rounds 3 --miner path/to.js      score a real submission
    run_round.py --rounds 0 --ext grammarly dark    just prove they load
    run_round.py --miner war-probe                  shows id probing FAILS now
    run_round.py --rounds 0 --no-headless --interact --slow 1 --hold 30

    docker compose exec challenge-api python3 /usr/local/bin/run_round.py

Dev only: this prints ground truth, which the API never may.
"""

import argparse
import functools
import json
import os
import re
import shutil
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import yaml

# In the image this script is copied to /usr/local/bin, which has no repo above
# it - so this is only meaningful in a checkout, and must not blow up elsewhere.
_parents = Path(__file__).resolve().parents
_REPO = _parents[4] if len(_parents) > 4 else Path("/nonexistent")
_API_DIR = Path(os.environ.get("EXC_CHALLENGE_API_DIR", "/app/rest-exc-challenge"))

# The app package is importable from both places; add whichever exists.
for _candidate in (_API_DIR, _REPO / "src/exc_challenge/challenge"):
    if (_candidate / "api").is_dir():
        sys.path.insert(0, str(_candidate))
        break

from api.endpoints.challenge._browser import (  # noqa: E402
    BAIT_HOST,
    BrowserError,
    BrowserSettings,
    ChromeSession,
)
from api.endpoints.challenge._payload_manager import PayloadManager  # noqa: E402

_ALL_URLS = ("<all_urls>", "*://*/*", "http://*/*", "https://*/*")


# ---------------------------------------------------------------- layout ----


@dataclass(frozen=True)
class Layout:
    """Where Chrome, the extensions and the bait page live."""

    chrome: Path
    driver: Path
    extensions: Path
    pool_file: Path
    bait_dir: Path
    scratch: Path


_CONTAINER_CHROME = Path("/opt/chrome/browser/chrome")

# A checkout, as laid down by the Chrome for Testing download.
_CHECKOUT_BINARIES = (
    (
        "volumes/chrome/chrome-mac-arm64/Google Chrome for Testing.app"
        "/Contents/MacOS/Google Chrome for Testing",
        "volumes/chrome/chromedriver-mac-arm64/chromedriver",
    ),
    (
        "volumes/chrome/chrome-linux64/chrome",
        "volumes/chrome/chromedriver-linux64/chromedriver",
    ),
)


def detect_layout() -> Layout:
    """The container's fixed paths if we are in it, otherwise a checkout."""
    if _CONTAINER_CHROME.is_file():
        return Layout(
            chrome=_CONTAINER_CHROME,
            driver=Path("/opt/chrome/driver/chromedriver"),
            extensions=Path("/opt/extensions"),
            pool_file=_API_DIR / "extensions.yml",
            bait_dir=_API_DIR / "templates",
            scratch=Path(
                os.environ.get(
                    "EXC_CHALLENGE_CHALLENGE_BROWSER_SCRATCH_DIR", "/run/exc"
                )
            ),
        )

    for chrome_rel, driver_rel in _CHECKOUT_BINARIES:
        chrome, driver = _REPO / chrome_rel, _REPO / driver_rel
        if chrome.is_file() and driver.is_file():
            return Layout(
                chrome=chrome,
                driver=driver,
                extensions=_REPO / "volumes/extensions",
                pool_file=_REPO / "src/exc_challenge/challenge/extensions.yml",
                bait_dir=_REPO / "src/exc_challenge/challenge/templates",
                scratch=_REPO / "volumes/scratch",
            )

    sys.exit(
        "Chrome for Testing not found. In a checkout it belongs under "
        "./volumes/chrome - see 'Running it locally' in docs/README.md"
    )


# ------------------------------------------------------------------ pool ----


@dataclass(frozen=True)
class Extension:
    id: str
    name: str
    group: str


def load_pool(pool_file: Path) -> list[Extension]:
    entries = (yaml.safe_load(pool_file.read_text(encoding="utf-8")) or {}).get("pool")
    if not entries:
        sys.exit(f"{pool_file} has an empty pool")
    missing_group = [e["id"] for e in entries if not e.get("group")]
    if missing_group:
        sys.exit(f"{pool_file}: pool entries with no group: {missing_group}")
    return [Extension(e["id"], e.get("name") or e["id"], e["group"]) for e in entries]


def groups_of(pool: list[Extension]) -> dict[str, list[str]]:
    """group name -> published names it owns, in first-seen order.

    Same shape as `_pool.load_pool_groups()`, which is what `run_script` wants:
    it filters each group's answer down to the names that group owns. Callers
    that only need the names iterate the keys.
    """
    groups: dict[str, list[str]] = {}
    for ext in pool:
        groups.setdefault(ext.group, []).append(ext.name)
    return groups


def select(pool: list[Extension], tokens: list[str] | None) -> list[Extension]:
    """Resolve ids or case-insensitive name fragments. None means all."""
    if not tokens:
        return list(pool)

    chosen: list[Extension] = []
    for token in tokens:
        needle = token.lower().replace(" ", "")
        matches = [
            e
            for e in pool
            if e.id == token or needle in e.name.lower().replace(" ", "")
        ]
        if not matches:
            sys.exit(
                f"no pool extension matches '{token}'. "
                f"Available: {', '.join(e.name for e in pool)}"
            )
        chosen.extend(matches)
    return list(dict.fromkeys(chosen))


# ------------------------------------------------------------- WAR probe ----


def _resolve(pattern: str, ext_dir: Path) -> str | None:
    """A real file inside `ext_dir` matching a web_accessible_resources pattern.

    Two traps, both found the hard way: a declared path is NOT proof the file
    exists (ColorZilla declares `css/content-style.css` and ships neither), and
    most WAR entries are patterns, where `*` matches `/` too.
    """
    if "*" not in pattern:
        return pattern if (ext_dir / pattern).is_file() else None

    regex = re.compile("^" + ".*".join(re.escape(p) for p in pattern.split("*")) + "$")
    for path in sorted(ext_dir.rglob("*")):
        if path.is_file():
            relative = path.relative_to(ext_dir).as_posix()
            if regex.match(relative):
                return relative
    return None


def war_probe_path(ext_id: str, ext_root: Path) -> str | None:
    """A concrete, fetchable web-accessible path for `ext_id`, if it has one.

    Read live from the unpacked manifest - the store re-publishes often and a
    pinned path would rot.
    """
    ext_dir = ext_root / ext_id
    manifest_file = ext_dir / "manifest.json"
    if not manifest_file.is_file():
        return None

    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    for block in manifest.get("web_accessible_resources") or []:
        # use_dynamic_url serves the resource under a per-session GUID that page
        # JS cannot enumerate, so it is unprobeable by this technique.
        if not isinstance(block, dict) or block.get("use_dynamic_url"):
            continue
        # Scoped to specific origins; our bait page is not one of them.
        if not any(m in _ALL_URLS for m in block.get("matches", [])):
            continue
        for resource in block.get("resources", []):
            if (resolved := _resolve(resource, ext_dir)) is not None:
                return resolved
    return None


def stub_miner(pool: list[Extension]) -> str:
    """All-false: a VALID submission that scores ~0. The default.

    It exists so `run_round.py` with no arguments exercises the real plumbing -
    staging, launch, the per-group fan-out, scoring - without teaching a
    technique. Replace it with `--miner path/to.js` once you have one.
    """
    return "\n".join(
        f"window.detect_{group} = async function () {{ return {{}}; }};"
        for group in groups_of(pool)
    )


def war_probe_miner(pool: list[Extension], probes: dict[str, str]) -> str:
    """Demonstrates that id probing DOES NOT WORK any more. Not a baseline.

    `fetch("chrome-extension://<store-id>/<path>")` used to identify installed
    extensions outright. It cannot now: manifests ship without `key`, so Chrome
    derives each id from the staging directory and every round uses a fresh
    one. Run this and every probe misses - that is the point of keeping it.

    One `window.detect_<group>` per group, matching the grouped-submission
    contract in `_browser.wrap_miner_script`. All of them land in the same
    staged file, which is fine: the wrapper looks them up on `window` and does
    not care which `<script src>` defined them - so this exercises the real
    per-group fan-out without needing `index.html` to grow 7 script tags.
    """
    functions = []
    for group in groups_of(pool):
        group_probes = {
            ext.name: [ext.id, probes[ext.id]]
            for ext in pool if ext.group == group and ext.id in probes
        }
        functions.append(f"""
        window.detect_{group} = async function () {{
            const found = {{}};
            const PROBES = {json.dumps(group_probes)};
            await Promise.all(Object.entries(PROBES).map(async ([name, probe]) => {{
                const [id, path] = probe;
                try {{
                    const res = await fetch(`chrome-extension://${{id}}/${{path}}`);
                    found[name] = res.ok;
                }} catch (_err) {{
                    found[name] = false;
                }}
            }}));
            return found;
        }};
        """)
    return "\n".join(functions)


# ------------------------------------------------------------ bait page ----


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        pass


@contextmanager
def bait_server(bait_dir: Path) -> Iterator[str]:
    """Serve the bait page on an ephemeral port; yield its url.

    Under the same hostname scoring uses (`BAIT_HOST`), so extensions that
    skip localhost behave here exactly as they do when scored. Chrome maps the
    name to this server - `launch(..., page_url)` passes it through. http, not
    file://: many extensions' content scripts only match http(s) pages.
    """
    if not bait_dir.is_dir():
        sys.exit(f"bait page directory missing: {bait_dir}")

    handler = functools.partial(_QuietHandler, directory=str(bait_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://{BAIT_HOST}:{server.server_port}/index.html"
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def chrome_on_bait_page(
    settings: BrowserSettings,
    tag: str,
    enabled: list[str],
    page_url: str,
    args: argparse.Namespace,
    narrate: bool = False,
) -> Iterator[ChromeSession]:
    """Launch with `enabled` loaded, open the bait page, settle, optionally
    drive it - the part both modes do identically."""
    with ChromeSession(settings, tag) as session:
        session.launch(sorted(enabled), page_url)
        session.open_page(page_url, args.settle)
        if args.interact:
            for step in session.interact(pause=args.slow):
                if narrate:
                    print(f"    {step}")
        yield session


# --------------------------------------------------------------- report ----


def print_verdicts(
    pool: list[Extension],
    enabled: set[str],
    predicted: dict[str, bool],
    probes: dict[str, str],
) -> int:
    """Truth beside prediction, one row per extension. Returns the miss count."""
    print(f"\n  {'extension':30} {'enabled':>8} {'detected':>9}   probe")
    print("  " + "-" * 78)
    misses = 0
    for ext in pool:
        truth = ext.id in enabled
        guess = predicted.get(ext.id, False)
        if truth != guess:
            misses += 1
        if not truth and ext.id not in probes:
            continue  # unprobeable and absent: nothing to say
        mark = "" if truth == guess else "  <-- MISS"
        print(
            f"  {ext.name[:30]:30} {str(truth):>8} {str(guess):>9}   "
            f"{probes.get(ext.id) or '(no static WAR path)'}{mark}"
        )
    return misses


# ----------------------------------------------------------------- main ----


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--rounds", type=int, default=1, help="0 = launch only, no miner")
    ap.add_argument(
        "--miner",
        default="stub",
        help="'stub' (all-false, valid, scores ~0), 'war-probe' (demonstrates "
        "that id probing no longer works), or a path to .js defining one or "
        "more window.detect_<group>",
    )
    ap.add_argument(
        "--ext", nargs="+", metavar="NAME_OR_ID", help="default: whole pool"
    )
    ap.add_argument("-k", type=int, default=4, help="extensions enabled per round")
    ap.add_argument(
        "--settle", type=float, default=5.0, help="seconds to wait after load"
    )
    ap.add_argument("--budget", type=float, default=20.0, help="miner script timeout")
    ap.add_argument(
        "--no-headless", action="store_true", help="show the browser window"
    )
    ap.add_argument(
        "--interact", action="store_true", help="click and type through the page"
    )
    ap.add_argument(
        "--slow", type=float, default=0.0, metavar="SEC", help="pause between steps"
    )
    ap.add_argument(
        "--hold", type=float, default=0.0, metavar="SEC", help="keep the browser open"
    )
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    layout = detect_layout()
    pool = select(load_pool(layout.pool_file), args.ext)
    ids = [e.id for e in pool]
    probes = {i: p for i in ids if (p := war_probe_path(i, layout.extensions))}

    layout.scratch.mkdir(parents=True, exist_ok=True)
    settings = BrowserSettings(
        chrome_bin=str(layout.chrome),
        chromedriver_bin=str(layout.driver),
        extensions_dir=str(layout.extensions),
        scratch_dir=str(layout.scratch),
        headless=not args.no_headless,
    )

    print(f"chrome:   {layout.chrome}")
    print(f"pool:     {len(pool)}  probeable: {len(probes)}  rounds: {args.rounds}")

    with bait_server(layout.bait_dir) as page_url:
        print(f"bait:     {page_url}\n")
        if not args.rounds:
            return _prove_they_load(pool, ids, settings, page_url, args)
        return _score_rounds(
            pool, ids, probes, settings, page_url, args, layout.bait_dir
        )


def _prove_they_load(
    pool: list[Extension],
    ids: list[str],
    settings: BrowserSettings,
    page_url: str,
    args: argparse.Namespace,
) -> int:
    """Enable everything selected and report what Chrome actually accepted.

    The only question here is whether a store .crx, unpacked with an injected
    `key`, loads under its REAL store id.
    """
    try:
        with chrome_on_bait_page(
            settings, "load", ids, page_url, args, narrate=True
        ) as session:
            print(f"  {'extension':30} {'store id':34} enabled")
            print(f"  {'-' * 30} {'-' * 34} -------")
            for ext in pool:
                print(
                    f"  {ext.name[:30]:30} {ext.id:34} "
                    f"{'YES' if ext.id in session.loaded else 'NO'}"
                )
            print(f"\n  page title: {session.driver.title!r}")
            if args.hold:
                time.sleep(args.hold)
    except BrowserError as err:
        print(f"\nFAILED: {err}", file=sys.stderr)
        return 1

    print(f"\nOK: all {len(pool)} extension(s) enabled under their real store ids")
    return 0


def _stage_miner_js(
    miner_js: str, bait_dir: Path, groups: Mapping[str, Sequence[str]]
) -> list[Path]:
    """Write the miner's code where every group's <script src> tag will find
    it - one copy per group file.

    `war_probe_miner()` (and a custom --miner file) already defines every
    `window.detect_<group>` it can in ONE string, and `index.html` loads all
    7 group files as separate <script> tags. Staging into only one would make
    load order decide which of two definitions for the same function wins -
    whichever tag runs last would stamp its (stub) definition over an earlier
    one. Writing the identical content into all 7 sidesteps that: by the time
    the last tag runs, every group is defined the same way regardless of order.

    The API does the same thing in `endpoints/challenge/utils.py`; this is the
    dev-tool copy so `run_round.py` keeps working from a checkout, where the
    app config (and therefore that module) may not import.
    """
    detections_dir = bait_dir / "static" / "detections"
    detections_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for group in groups:
        target = detections_dir / f"{group}.js"
        backup = target.with_suffix(".js.stub")
        if target.is_file() and not backup.exists():
            shutil.copy2(target, backup)
        target.write_text(miner_js, encoding="utf-8")
        staged.append(target)
    return staged


def _restore_stub(staged: list[Path]) -> None:
    """Put the checked-in stubs back. Never raises - it runs in a `finally`.

    Mirrors `utils.restore_stubs()`, including the else-branch: with no backup
    there was no file before this run, so the miner's code must be deleted, not
    left to become the next run's "stub".
    """
    for target in staged:
        backup = target.with_suffix(".js.stub")
        try:
            if backup.is_file():
                shutil.copy2(backup, target)
            else:
                target.unlink(missing_ok=True)
        except OSError as err:
            print(f"warning: could not restore {target.name}: {err}", file=sys.stderr)


def _score_rounds(
    pool: list[Extension],
    ids: list[str],
    probes: dict[str, str],
    settings: BrowserSettings,
    page_url: str,
    args: argparse.Namespace,
    bait_dir: Path,
) -> int:
    if args.miner == "stub":
        miner_js = stub_miner(pool)
    elif args.miner == "war-probe":
        miner_js = war_probe_miner(pool, probes)
    else:
        miner_path = Path(args.miner)
        if miner_path.is_dir():
            miner_js = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(miner_path.glob("*.js"))
            )
        else:
            miner_js = miner_path.read_text(encoding="utf-8")
    groups = groups_of(pool)
    # The miner's code is no longer injected at sample time - the bait page
    # loads it with a <script src>, so it has to be on disk before Chrome
    # navigates. Same staging the API does, restored on the way out.
    staged = _stage_miner_js(miner_js, bait_dir, groups)
    k = min(args.k, len(ids))
    manager = PayloadManager(ids)
    manager.build_schedule(args.rounds, k)

    misses = 0
    try:
        for rec in manager.rounds:
            names = sorted(e.name for e in pool if e.id in rec.enabled)
            print(f"[round {rec.index}] enabling {len(rec.enabled)}: {', '.join(names)}")

            started = time.monotonic()
            try:
                with chrome_on_bait_page(
                    settings, f"run-{rec.index}", sorted(rec.enabled), page_url, args
                ) as session:
                    predicted_by_name = session.run_script(
                        [ext.name for ext in pool], groups, args.budget
                    )
                    predicted = {
                        ext.id: predicted_by_name.get(ext.name, False)
                        for ext in pool
                    }
            except BrowserError as err:
                manager.record(rec.index, None, error=str(err))
                print(f"    -> FAILED after {time.monotonic() - started:.1f}s: {err}\n")
                continue

            elapsed = time.monotonic() - started
            score = manager.record(rec.index, predicted, duration_sec=round(elapsed, 2))
            misses += print_verdicts(pool, rec.enabled, predicted, probes)
            print(f"    -> score {score:.4f}  ({elapsed:.1f}s)\n")

    finally:
        _restore_stub(staged)

    print(f"run score: {manager.calculate_score():.4f}")

    # Enabled extensions with no static WAR path are invisible to the baseline
    # probe by design, so they do not count as failures.
    blind = sum(1 for r in manager.rounds for i in r.enabled if i not in probes)
    if blind:
        print(f"note: {blind} enabled extension(s) had no static WAR path")
    return 0 if misses <= blind else 1


if __name__ == "__main__":
    raise SystemExit(main())
