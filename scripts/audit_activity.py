#!/usr/bin/env python3
"""Which pool extensions actually DO something to the page?

    docker compose exec challenge-api python3 /usr/local/bin/audit_activity.py

A `chrome-extension://<id>/file.css` fetch resolves because the extension is
INSTALLED - it resolves just as well when the extension's logic never runs. So
reachability says nothing about activity, and an extension that loads but does
nothing is unlabelable in principle: no miner can detect what never happens.

Launches each extension alone, diffs the page and the network against a
no-extension baseline, and reports `active` or `inert`. Also measures
time_to_stable_ms, which is where `settle_seconds` should come from.

One browser at a time - concurrent Chromes are what exhausts RAM.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

API_DIR = Path(os.environ.get("EXC_CHALLENGE_API_DIR", "/app/rest-exc-challenge"))
sys.path.insert(0, str(API_DIR))

_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("--max-settle", type=float, default=12.0, metavar="SEC")
_ap.add_argument("--min-settle", type=float, default=5.0, metavar="SEC")
_ap.add_argument("--poll", type=float, default=0.5, metavar="SEC")
_ap.add_argument("--gesture-settle", type=float, default=2.0, metavar="SEC")
_ap.add_argument("--only", nargs="+", metavar="ID")
_ap.add_argument(
    "--pool",
    metavar="YML",
    help="audit this candidate list instead of the shipped pool. The config "
    "file's pool_path outranks the environment, so this is the only way to "
    "audit candidates without editing extensions.yml",
)
_ap.add_argument("--out", default="/tmp/activity_audit.json")
ARGS = _ap.parse_args()
# `api.config` builds a pydantic CliSettingsSource with cli_parse_args=True, so
# importing it parses sys.argv and rejects anything it does not recognise. Args
# are taken above, then argv is hidden from it.
sys.argv = sys.argv[:1]


from api.config import config  # noqa: E402
from api.endpoints.challenge._browser import BrowserSettings, ChromeSession  # noqa: E402
from api.endpoints.challenge._pool import load_name_to_id  # noqa: E402
from api.endpoints.challenge.service import bait_page_url  # noqa: E402

# One host per known blocker fingerprint, plus a control nothing blocks. If the
# control fails there is no egress and every "blocked" reading is meaningless.
_HOSTS = {
    "control": "https://example.com/",
    "pagead": "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",
    "ga": "https://www.google-analytics.com/analytics.js",
    "amazon": "https://c.amazon-adsystem.com/aax2/apstag.js",
    "segment": "https://cdn.segment.com/analytics.js/v1/x/analytics.min.js",
}

# Everything an extension might disturb, in one cheap call.
_SIGNATURE = """
const attrs = (el) => el ? Array.from(el.attributes).map(a => a.name).sort().join() : '';
return {
  nodes: document.getElementsByTagName('*').length,
  styles: document.querySelectorAll('style').length,
  shadow: Array.from(document.querySelectorAll('*')).filter(e => e.shadowRoot).length,
  bodyBg: getComputedStyle(document.body).backgroundColor,
  htmlAttrs: attrs(document.documentElement),
  pwAttrs: attrs(document.querySelector('input[type=password]')),
  adsGone: ['#ad-banner', '.adsbygoogle', '.ad-slot']
    .filter(s => !document.querySelector(s) ||
                 document.querySelector(s).offsetHeight === 0).length,
};
"""

# What privacy and fingerprinting extensions change: browser APIs, not the
# DOM. The DOM signature above cannot see any of this - it reported Random
# User-Agent "inert" while it was rewriting navigator.userAgent. Reads only;
# nothing here draws a canvas or samples audio, so every value is repeatable.
_API_SIGNATURE = """
const native = (f) => {
  try { return Function.prototype.toString.call(f).includes('[native code]'); }
  catch (e) { return 'err'; }
};
const getter = (proto, key) => {
  const d = Object.getOwnPropertyDescriptor(proto, key);
  return d && d.get ? native(d.get) : 'none';
};
const geo = navigator.geolocation;
return {
  ua: navigator.userAgent, platform: navigator.platform,
  languages: String(navigator.languages),
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  tzOffset: new Date().getTimezoneOffset(),
  hidden: String(document.hidden), visibility: document.visibilityState,
  cores: navigator.hardwareConcurrency, memory: navigator.deviceMemory,
  nativeCanvas: native(HTMLCanvasElement.prototype.toDataURL),
  nativeCanvasRead: native(CanvasRenderingContext2D.prototype.getImageData),
  nativeText: native(CanvasRenderingContext2D.prototype.measureText),
  nativeAudio: native(AudioBuffer.prototype.getChannelData),
  nativeWebgl: native(WebGLRenderingContext.prototype.getParameter),
  nativeGpu: navigator.gpu ? native(navigator.gpu.requestAdapter) : 'none',
  nativeGeo: geo ? native(geo.getCurrentPosition) : 'none',
  geoProto: geo ? Object.getPrototypeOf(geo) === Geolocation.prototype : 'none',
  nativeRtc: typeof RTCPeerConnection === 'function' ? native(RTCPeerConnection)
             : typeof RTCPeerConnection,
  nativeTz: native(Date.prototype.getTimezoneOffset),
  nativeOffsetWidth: getter(HTMLElement.prototype, 'offsetWidth'),
  nativeCores: getter(Navigator.prototype, 'hardwareConcurrency'),
  nativeShadow: native(Element.prototype.attachShadow),
  // Hooks disguised as native code only show in behaviour: a noise extension
  // makes the same canvas read differently twice, and a silent buffer non-zero.
  canvasRepeatable: (() => {
    const c = document.createElement('canvas'); c.width = 64; c.height = 16;
    const g = c.getContext('2d'); g.fillStyle = '#f60'; g.fillRect(0, 0, 64, 16);
    g.fillStyle = '#069'; g.font = '11px Arial'; g.fillText('exc-audit', 2, 12);
    return c.toDataURL() === c.toDataURL();
  })(),
  audioSilent: new AudioBuffer({length: 256, sampleRate: 44100})
    .getChannelData(0).every((v) => v === 0),
  ownHidden: Object.getOwnPropertyNames(document)
    .filter((k) => /hidden|visibility/i.test(k)).join(),
  nativeHidden: getter(Document.prototype, 'hidden'),
  nativeVisibility: getter(Document.prototype, 'visibilityState'),
  nativeListener: native(EventTarget.prototype.addEventListener),
  windowSymbols: Object.getOwnPropertySymbols(window).length,
  dateProtoKeys: Object.getOwnPropertyNames(Date.prototype).length,
  documentOwnKeys: Object.getOwnPropertyNames(document).length,
};
"""

_FETCH_ALL = """
const cb = arguments[arguments.length - 1];
(async () => {
  const out = {};
  for (const [name, url] of Object.entries(arguments[0])) {
    try { await fetch(url, {mode: 'no-cors', cache: 'no-store'}); out[name] = false; }
    catch (e) { out[name] = true; }        // threw => blocked
  }
  cb(out);
})();
"""


def probe(session, page_url: str, args) -> dict:
    """Navigate, wait until the page stops changing, gesture, sample."""
    driver = session.driver
    driver.set_script_timeout(120)

    started = time.monotonic()
    driver.get(page_url)

    # Poll until the signature repeats: measured, not assumed.
    previous = None
    stable_ms = None
    while time.monotonic() - started < args.max_settle:
        sig = driver.execute_script(_SIGNATURE)
        if sig == previous:
            stable_ms = int((time.monotonic() - started) * 1000)
            break
        previous = sig
        time.sleep(args.poll)

    # The DOM settles well before declarativeNetRequest does - a blocker's
    # rulesets are still compiling when the page already looks finished.
    # Sampling at DOM-stable reports every blocker as inert.
    time.sleep(max(0.0, args.min_settle - (time.monotonic() - started)))

    session.interact()  # password managers inject only after a real gesture
    time.sleep(args.gesture_settle)

    return {
        "stable_ms": stable_ms,
        "sig": driver.execute_script(_SIGNATURE),
        "api": driver.execute_script(_API_SIGNATURE),
        "blocked": driver.execute_async_script(_FETCH_ALL, _HOSTS),
    }


def classify(base: dict, got: dict) -> tuple[str, list[str], list[str]]:
    """Compare one extension's readings against the no-extension baseline.

    `dom` covers both page content and browser APIs - an extension that
    changes either is detectable. API differences are prefixed `api:`.
    """
    dom = sorted(k for k in base["sig"] if base["sig"][k] != got["sig"][k])
    dom += sorted(f"api:{k}" for k in base["api"] if base["api"][k] != got["api"][k])
    blocks = sorted(
        host
        for host, was_blocked in got["blocked"].items()
        if host != "control" and was_blocked and not base["blocked"][host]
    )
    return ("active" if (dom or blocks) else "inert"), dom, blocks


def report(results: dict, args) -> None:
    print(f"\n{'extension':<26} {'verdict':<8} {'stable':>8}  signals")
    print("-" * 78)
    for r in results.values():
        if r["verdict"] == "ERROR":
            print(f"{r['name'][:26]:<26} ERROR")
            continue
        signals = " ".join(r["dom"] + [f"blocks:{b}" for b in r["blocks"]]) or "-"
        stable = f"{r['stable_ms']}ms"
        print(f"{r['name'][:26]:<26} {r['verdict']:<8} {stable:>8}  {signals}")

    inert = [r["name"] for r in results.values() if r["verdict"] == "inert"]
    active = sum(1 for r in results.values() if r["verdict"] == "active")
    print(f"\nactive: {active}  inert: {len(inert)}  -> {inert}")

    stable = sorted(r["stable_ms"] for r in results.values() if r.get("stable_ms"))
    if stable:
        print(f"DOM settles: median={stable[len(stable) // 2]}ms max={stable[-1]}ms")
        print(
            f"NOTE: settle time is measured on the DOM only; every sample is taken\n"
            f"      after at least {args.min_settle:.0f}s so slower changes are counted too."
        )


def main() -> int:
    args = ARGS
    if args.pool:
        # Before anything reads the pool: the loaders are cached on first use.
        config.challenge.pool_path = args.pool

    # Curation tool: it launches real extensions, so it works in store ids
    # (the directory names under /opt/extensions), not published names.
    pool = list(load_name_to_id().values())
    if args.only:
        pool = [i for i in pool if i in set(args.only)]
    settings = BrowserSettings(**config.challenge.browser.declared_dump())
    # The exact page production scores on. Never hand-written here: a copy
    # drifted to `/_web/index.html`, which renders empty, and every audit
    # measured a blank page.
    page_url = bait_page_url()
    names = {_id: _name for _name, _id in load_name_to_id().items()}

    def run(ext_ids: list[str], tag: str) -> dict | None:
        try:
            with ChromeSession(settings, tag) as session:
                session.launch(ext_ids, page_url)
                return probe(session, page_url, args)
        except Exception as err:  # noqa: BLE001 - one bad extension must not stop the audit
            print(f"    FAILED: {str(err)[:100]}")
            return None

    print(f"auditing {len(pool)} extension(s), one at a time\n")
    base = run([], "audit-base")
    if base is None:
        print("baseline failed; nothing to compare against")
        return 1
    if base["blocked"]["control"]:
        print("WARNING: no egress - blocking results are meaningless")
    else:
        print("NOTE: control host reachable, egress OK")
    print(f"baseline stable at {base['stable_ms']}ms\n")

    results: dict[str, dict] = {}
    for n, ext_id in enumerate(pool, 1):
        name = names.get(ext_id, ext_id)
        print(f"[{n}/{len(pool)}] {name}")
        got = run([ext_id], f"audit-{n}")
        if got is None:
            results[ext_id] = {"name": name, "verdict": "ERROR"}
            continue

        verdict, dom, blocks = classify(base, got)
        results[ext_id] = {
            "name": name,
            "verdict": verdict,
            "dom": dom,
            "blocks": blocks,
            "stable_ms": got["stable_ms"],
        }
        print(
            f"    {verdict:6} stable={got['stable_ms']}ms "
            f"dom={dom or '-'} blocks={blocks or '-'}"
        )

    Path(args.out).write_text(json.dumps(results, indent=2))
    report(results, args)
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
