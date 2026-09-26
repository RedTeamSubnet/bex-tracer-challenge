#!/usr/bin/env python3
"""Run the full challenge loop locally, the way the validator runs it in prod.

Three hops, no shortcuts:

    1. GET  challenge/task    -> the published pool (27 extension ids)
    2. POST miner/solve       -> the miner returns solution.js as a commit file
    3. POST challenge/score   -> real Chrome rounds, one float back

Nothing here reaches inside the challenge. It only speaks HTTP, so whatever
score it prints is the score a real validator would have given.

    scripts/run_local_challenge.py
    scripts/run_local_challenge.py --solution examples/miner_commit/src/commit/solution.js
    scripts/run_local_challenge.py --api-key "$BEX_CHALLENGE_API_KEY"

Round count is server-side config (challenge.yml: n_rounds), not a flag here.

Exit code is 0 on a scored run, 1 on any failure.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_CHALLENGE = "http://127.0.0.1:10001"
DEFAULT_MINER = "http://127.0.0.1:10002"
DEFAULT_API_KEY = "challenge_api_key"


class StepFailed(RuntimeError):
    """One hop failed. The message already says which and why."""


def _request(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 30.0,
) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as err:
        body = err.read().decode(errors="replace")[:400]
        raise StepFailed(f"{url} -> HTTP {err.code}\n  {body}") from err
    except urllib.error.URLError as err:
        raise StepFailed(f"{url} unreachable: {err.reason}") from err


def wait_for_health(base: str, label: str, attempts: int = 30) -> None:
    for _ in range(attempts):
        try:
            _request(f"{base}/health", timeout=3.0)
            return
        except StepFailed:
            time.sleep(1.0)
    raise StepFailed(
        f"{label} at {base} never became healthy. Start it first:\n"
        f"  challenge: docker compose up -d\n"
        f"  miner    : docker compose -f examples/miner_commit/compose.yml up -d --build"
    )


def get_task(base: str) -> dict[str, Any]:
    task = _request(f"{base}/task")
    ids = task.get("extension_ids") or []
    if not ids:
        raise StepFailed(f"{base}/task returned an empty pool")
    return task


def solve_via_miner(base: str, task: dict[str, Any]) -> dict[str, Any]:
    out = _request(f"{base}/solve", payload=task, timeout=60.0)
    files = out.get("commit_files") or []
    if not files:
        raise StepFailed(f"{base}/solve returned no commit files")
    return out


def solve_from_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise StepFailed(f"no such solution file: {path}")
    return {"commit_files": [{"file_name": "solution.js", "content": path.read_text()}]}


def score(base: str, task: dict[str, Any], output: dict[str, Any], key: str) -> float:
    result = _request(
        f"{base}/score",
        payload={"miner_input": task, "miner_output": output},
        api_key=key,
        timeout=3600.0,
    )
    if not isinstance(result, (int, float)):
        raise StepFailed(f"{base}/score returned {type(result).__name__}: {result!r}")
    return float(result)


_KEY_VAR = "BEX_CHALLENGE_API_KEY"


def resolve_api_key(repo_root: Path) -> tuple[str, str]:
    """Same precedence the container sees: env var, then .env, then the default.

    Returns (key, where_it_came_from) so the run can say which one it used
    without ever printing the key itself.
    """
    from_env = os.environ.get(_KEY_VAR)
    if from_env:
        return from_env, f"${_KEY_VAR}"

    dotenv = repo_root / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{_KEY_VAR}="):
                value = line.split("=", 1)[1].strip().strip("'\"")
                if value:
                    return value, ".env"

    return DEFAULT_API_KEY, "built-in default"


def _parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--challenge", default=DEFAULT_CHALLENGE)
    ap.add_argument("--miner", default=DEFAULT_MINER)
    ap.add_argument(
        "--api-key",
        default=None,
        help=f"defaults to ${_KEY_VAR}, then .env, then the built-in default",
    )
    ap.add_argument(
        "--solution",
        type=Path,
        default=None,
        metavar="FILE",
        help="skip the miner service and submit this .js directly",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch the task and the miner's answer, but do not score",
    )
    ap.epilog = f"repo root: {repo_root}"
    return ap.parse_args()


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parent.parent

    if args.api_key:
        api_key, key_source = args.api_key, "--api-key"
    else:
        api_key, key_source = resolve_api_key(repo_root)

    try:
        print(f"[1/3] task     {args.challenge}/task")
        wait_for_health(args.challenge, "challenge")
        task = get_task(args.challenge)
        print(f"      pool of {len(task['extension_ids'])} extension(s)")

        if args.solution:
            print(f"[2/3] solution {args.solution}")
            output = solve_from_file(args.solution)
        else:
            print(f"[2/3] solve    {args.miner}/solve")
            wait_for_health(args.miner, "miner")
            output = solve_via_miner(args.miner, task)

        for f in output["commit_files"]:
            lines = len(f["content"].splitlines())
            flag = "  <-- OVER THE 500-LINE CAP" if lines > 500 else ""
            print(f"      {f['file_name']}: {lines} line(s){flag}")

        if args.dry_run:
            print("[3/3] skipped (--dry-run)")
            return 0

        print(f"[3/3] score    {args.challenge}/score")
        print(f"      api key from {key_source}")
        print("      running real Chrome rounds, this takes minutes...")
        started = time.monotonic()
        value = score(args.challenge, task, output, api_key)
        elapsed = time.monotonic() - started

    except StepFailed as err:
        print(f"\nFAILED: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1

    print(f"\n  score {value:.4f}   ({elapsed:.0f}s)")
    if value <= 0.0:
        print(
            "  0.0 means no information: an all-one-class answer, or every round failed."
        )
    elif value >= 1.0:
        print("  1.0 is a perfect run. Verify the pool is not trivially detectable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
