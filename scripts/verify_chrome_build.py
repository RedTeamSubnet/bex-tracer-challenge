#!/usr/bin/env python3
"""Build-time gates for the challenge image. BUILD TIME ONLY.

Every check here guards a failure mode that is otherwise SILENT at runtime: a
Chrome that cannot start, or extensions that never load, make `/score` return
0.0 for every miner while the container looks perfectly healthy. Failing the
image is the only place this is cheap to notice.

Deliberately standalone - it does NOT import `api.*`. The app config is not
present during the build, and a gate that imported the code it is testing would
prove less.

    python3 scripts/verify_chrome_build.py --expect-version 152.0.7977.54
"""

import argparse
import base64
import hashlib
import json
import shutil
import subprocess  # nosec B404 - fixed binary paths, no user input
import sys
import tempfile
from pathlib import Path

CHROME_BIN = Path("/opt/chrome/browser/chrome")
DRIVER_BIN = Path("/opt/chrome/driver/chromedriver")
EXT_ROOT = Path("/opt/extensions")
POOL_PATH = Path("/app/extensions.yml")


class GateFailed(Exception):
    """One gate failed. All gates still run, so the log shows every problem."""


def derive_id(spki_der: bytes) -> str:
    """Chrome's extension id: first 16 bytes of sha256(public key), hex mapped
    onto a-p. This is what Chrome will compute from the injected `key`."""
    digest = hashlib.sha256(spki_der).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def gate_shared_libraries() -> str:
    """A missing .so is the single most likely way this image is broken: the
    Debian trixie t64 library names differ from bookworm's."""
    result = subprocess.run(  # nosec B603 B607 - build-time gate, constant argv
        ["ldd", str(CHROME_BIN)], capture_output=True, text=True, check=False
    )
    missing = [
        line.strip() for line in result.stdout.splitlines() if "not found" in line
    ]
    if missing:
        raise GateFailed(
            "chrome has unresolved shared libraries:\n    " + "\n    ".join(missing)
        )
    return f"ldd resolved every library ({len(result.stdout.splitlines())} entries)"


def gate_versions(expected: str) -> str:
    for binary in (CHROME_BIN, DRIVER_BIN):
        if not binary.is_file():
            raise GateFailed(f"{binary} is missing")

    chrome_out = subprocess.run(  # nosec B603 - constant argv
        [str(CHROME_BIN), "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if expected not in chrome_out:
        raise GateFailed(f"chrome reports {chrome_out!r}, expected {expected}")

    driver_out = subprocess.run(  # nosec B603 - constant argv
        [str(DRIVER_BIN), "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    if expected not in driver_out:
        # A driver/browser mismatch fails every session with an opaque error.
        raise GateFailed(f"chromedriver reports {driver_out!r}, expected {expected}")

    return f"{chrome_out} / {driver_out}"


def unpacked_ids() -> list[str]:
    if not EXT_ROOT.is_dir():
        raise GateFailed(f"{EXT_ROOT} does not exist")
    return sorted(p.name for p in EXT_ROOT.iterdir() if p.is_dir())


def gate_pool_is_unpacked() -> str:
    """Every id the API will publish must have been unpacked into the image."""
    import yaml  # available: PyYAML is in requirements.txt

    if not POOL_PATH.is_file():
        raise GateFailed(f"{POOL_PATH} is missing - the API cannot publish a pool")

    spec = yaml.safe_load(POOL_PATH.read_text(encoding="utf-8")) or {}
    pool = [e["id"] for e in (spec.get("pool") or []) if e.get("id")]
    if not pool:
        raise GateFailed(f"{POOL_PATH} has an empty pool")

    on_disk = set(unpacked_ids())
    absent = [ext_id for ext_id in pool if ext_id not in on_disk]
    if absent:
        raise GateFailed(f"pool ids not unpacked into {EXT_ROOT}: {absent}")

    return f"all {len(pool)} pool ids unpacked"


def gate_ids_match_keys() -> str:
    """The injected `key` must derive the pinned id.

    Without it Chrome derives the id from the directory path, every
    `chrome-extension://<real-id>/` probe fails, and the challenge silently
    becomes unsolvable by any published technique.
    """
    problems = []
    checked = 0

    for ext_id in unpacked_ids():
        if "," in ext_id:
            # --load-extension is comma-separated; a comma splits one path in two.
            problems.append(f"{ext_id}: id contains a comma")
            continue

        manifest_path = EXT_ROOT / ext_id / "manifest.json"
        if not manifest_path.is_file():
            problems.append(f"{ext_id}: no manifest.json")
            continue

        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        if manifest.get("manifest_version") != 3:
            problems.append(
                f"{ext_id}: manifest_version {manifest.get('manifest_version')}"
            )
            continue

        key = manifest.get("key")
        if not key:
            problems.append(
                f"{ext_id}: manifest has no `key` - id will be path-derived"
            )
            continue

        derived = derive_id(base64.b64decode(key))
        if derived != ext_id:
            problems.append(f"{ext_id}: injected key derives {derived}")
            continue

        checked += 1

    if problems:
        raise GateFailed("extension key/id problems:\n    " + "\n    ".join(problems))
    return f"{checked} extension(s) derive their pinned id from the injected key"


def gate_headless_launch_loads_an_extension() -> str:
    """The end-to-end proof: start real Chrome, load one real extension, and
    confirm Chrome itself reports it as installed."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    candidates = unpacked_ids()
    if not candidates:
        raise GateFailed(f"no extensions unpacked under {EXT_ROOT}")
    ext_id = candidates[0]

    scratch = Path(tempfile.mkdtemp(prefix="gate-"))
    staged = scratch / ext_id
    # Chrome rewrites _metadata/ on load, so it needs a writable copy.
    shutil.copytree(EXT_ROOT / ext_id, staged)

    options = Options()
    options.binary_location = str(CHROME_BIN)
    for arg in (
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-first-run",
        "--no-default-browser-check",
        f"--user-data-dir={scratch / 'profile'}",
        f"--load-extension={staged}",
    ):
        options.add_argument(arg)

    driver = None
    try:
        driver = webdriver.Chrome(
            service=Service(executable_path=str(DRIVER_BIN)), options=options
        )
        driver.get("chrome://extensions-internals/")
        entries = json.loads(driver.find_element("tag name", "pre").text)
        loaded = {e["id"] for e in entries if isinstance(e, dict) and "id" in e}
    except Exception as err:  # any failure here is a failed gate
        raise GateFailed(
            f"headless launch failed: {type(err).__name__}: {err}"
        ) from err
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:  # nosec B110 - teardown must not mask a gate failure
                pass
        shutil.rmtree(scratch, ignore_errors=True)

    if ext_id not in loaded:
        raise GateFailed(
            f"chrome started but did not load {ext_id} (reported: {sorted(loaded)})"
        )
    return f"chrome loaded {ext_id} and reported it via chrome://extensions-internals/"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expect-version", required=True)
    args = ap.parse_args()

    gates = [
        ("shared libraries", gate_shared_libraries),
        ("chrome + chromedriver version", lambda: gate_versions(args.expect_version)),
        ("pool is unpacked", gate_pool_is_unpacked),
        ("ids match injected keys", gate_ids_match_keys),
        ("headless launch loads an extension", gate_headless_launch_loads_an_extension),
    ]

    failures = 0
    for name, gate in gates:
        try:
            detail = gate()
        except GateFailed as err:
            failures += 1
            print(f"  FAIL  {name}\n        {err}", file=sys.stderr)
        except Exception as err:
            failures += 1
            print(
                f"  FAIL  {name}\n        unexpected {type(err).__name__}: {err}",
                file=sys.stderr,
            )
        else:
            print(f"  OK    {name}: {detail}")

    if failures:
        print(f"\n{failures} of {len(gates)} build gate(s) FAILED", file=sys.stderr)
        return 1
    print(f"\nall {len(gates)} build gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
