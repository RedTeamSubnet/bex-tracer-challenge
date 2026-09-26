#!/usr/bin/env python3
"""Download, verify and unpack the extension pool. BUILD TIME ONLY.

Reads `extensions.yml` (names) and `extensions.lock.yml` beside it (store id,
version and sha256 per name - private, not in the public repository), and per
extension:
    download .crx -> verify sha256 -> parse CRX3 -> assert the signature id
    matches the pin -> unzip

`key` is deliberately NOT injected into manifest.json. Store .crx manifests
carry no `key` field (the Web Store rejects uploads that have one; Chrome
injects it at install time in SandboxedUnpacker, which loading unpacked
bypasses). Leaving it out means Chrome derives the extension id from the
*directory path* instead - and `_browser.py` stages every round under
`/run/exc/round-<tag>/`, so each extension gets a DIFFERENT id every round.

That is the point. A miner cannot hardcode `chrome-extension://<id>/...`
against an id that does not exist until the round starts, so WAR probing from
a published id table - and every piece of public prior-art keyed by store id -
stops working. Measured: Dark Reader staged at two paths came back as
`laacekkl...` and `adjcpmpl...`, neither of them its store id.

The store id is still used at BUILD time: it names the unpacked directory and
`parse_crx3` checks it against the pin, which is what verifies we downloaded
the extension we meant to. It just never reaches a running browser.

Usage:
    python3 scripts/fetch_extensions.py --out /opt/extensions
    python3 scripts/fetch_extensions.py --out ./volumes/extensions --allow-sha-mismatch
"""

import argparse
import hashlib
import io
import json
import shutil
import struct
import sys
import urllib.request
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

CRX_URL = (
    "https://clients2.google.com/service/update2/crx"
    "?response=redirect&acceptformat=crx2,crx3"
    "&prodversion={ver}&x=id%3D{eid}%26uc"
)
_CRX_MAGIC = b"Cr24"
_DEFAULT_CFT_VERSION = "152.0.7977.54"


class ExtensionError(Exception):
    """One extension failed; the run continues and reports it at the end."""


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def _fields(buf: bytes) -> Iterator[tuple[int, Any]]:
    """Minimal protobuf wire-format scanner: yields (field_number, value)."""
    i = 0
    while i < len(buf):
        key, i = _varint(buf, i)
        field_no, wire_type = key >> 3, key & 7
        if wire_type == 0:
            value, i = _varint(buf, i)
        elif wire_type == 2:
            length, i = _varint(buf, i)
            value = buf[i : i + length]
            i += length
        elif wire_type == 5:
            value = buf[i : i + 4]
            i += 4
        elif wire_type == 1:
            value = buf[i : i + 8]
            i += 8
        else:
            raise ValueError(f"bad protobuf wire type {wire_type}")
        yield field_no, value


def encode_crx_id(digest: bytes) -> str:
    """Chromium crx_file::id_util - 16 digest bytes, nibbles 0-f mapped to a-p."""
    return "".join(chr(97 + (b >> 4)) + chr(97 + (b & 0xF)) for b in digest)


def crx_id_from_pubkey(spki_der: bytes) -> str:
    """Chromium crx_file::id_util::GenerateId - SHA256[:16], nibbles 0-f -> a-p."""
    return encode_crx_id(hashlib.sha256(spki_der).digest()[:16])


def parse_crx3(data: bytes) -> tuple[str, bytes, bytes]:
    """Returns (extension_id, spki_der_public_key, zip_bytes)."""
    magic, version, header_len = struct.unpack("<4sII", data[:12])
    if magic != _CRX_MAGIC:
        raise ValueError(f"not a CRX file (magic={magic!r})")
    if version != 3:
        raise ValueError(f"unsupported CRX version {version} (CRX2 is dead)")

    header = data[12 : 12 + header_len]
    zip_bytes = data[12 + header_len :]

    declared_id: str | None = None
    pubkeys: list[bytes] = []
    for field_no, value in _fields(header):
        if field_no in (2, 3):  # sha256_with_rsa / sha256_with_ecdsa
            for sub_no, sub_val in _fields(value):  # AsymmetricKeyProof
                if sub_no == 1:
                    pubkeys.append(sub_val)
        elif field_no == 10000:  # signed_header_data
            for sub_no, sub_val in _fields(value):  # SignedData
                if sub_no == 1 and len(sub_val) == 16:
                    declared_id = encode_crx_id(sub_val)

    if declared_id is None:
        raise ValueError("CRX3 header has no signed_header_data.crx_id")

    # CRITICAL: headers carry several proofs. Taking the first gives the wrong
    # id silently. Pick the one whose key hashes to the declared crx_id.
    for pubkey in pubkeys:
        if crx_id_from_pubkey(pubkey) == declared_id:
            return declared_id, pubkey, zip_bytes

    raise ValueError(
        f"no AsymmetricKeyProof matches declared crx_id {declared_id} "
        f"(candidates: {[crx_id_from_pubkey(p) for p in pubkeys]})"
    )


def _extract_without_traversal(zip_bytes: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        root = dest.resolve()
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"zip traversal attempt: {info.filename}")
        zf.extractall(dest)


def _drop_chrome_metadata(dest: Path) -> None:
    """Chrome deletes these on every unpacked load; drop them now so the
    on-disk tree is byte-stable."""
    for junk in ("_metadata/verified_contents.json", "_metadata/computed_hashes.json"):
        (dest / junk).unlink(missing_ok=True)
    meta = dest / "_metadata"
    if meta.is_dir() and not any(meta.iterdir()):
        meta.rmdir()


def unpack_crx(crx_bytes: bytes, dest: Path, *, expected_id: str) -> dict[str, Any]:
    ext_id, _spki, zip_bytes = parse_crx3(crx_bytes)
    if ext_id != expected_id:
        raise ValueError(f"CRX id {ext_id} != pinned {expected_id}")

    # A pin refresh overlays the new zip onto the old tree; files dropped
    # between versions would survive and keep answering WAR probes that should
    # now fail. Start from an empty directory.
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    _extract_without_traversal(zip_bytes, dest)
    _drop_chrome_metadata(dest)

    manifest_path = dest / "manifest.json"
    # utf-8-sig: real store manifests ship BOMs and a plain utf-8 read fails
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))

    if manifest.get("manifest_version") != 3:
        raise ValueError(
            f"{expected_id} is manifest_version "
            f"{manifest.get('manifest_version')}; Chrome 152 hard-rejects MV2"
        )

    # `key` is REMOVED, not merely not-injected. Most store manifests carry no
    # `key` (the Web Store rejects uploads that have one), but some ship it
    # anyway - NordPass does - and such an extension would keep a stable id
    # while every other one rotated, handing a miner exactly the WAR-probe
    # foothold this is meant to close. Popping covers both cases.
    manifest.pop("key", None)
    manifest.pop("update_url", None)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {"id": ext_id, "version": manifest.get("version")}


def download(url: str, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return resp.read()


def _display_name(entry: dict[str, Any]) -> str:
    return entry.get("name", entry["id"])


def check_id_has_no_comma(entry: dict[str, Any]) -> None:
    """--load-extension takes a comma-separated list, so an id containing a
    comma would silently split into two bogus paths."""
    if "," in entry["id"]:
        raise ExtensionError(f"{_display_name(entry)}: id contains a comma")


def download_crx(entry: dict[str, Any], *, cft_version: str) -> bytes:
    try:
        return download(CRX_URL.format(ver=cft_version, eid=entry["id"]))
    except Exception as err:
        raise ExtensionError(f"{_display_name(entry)}: download failed: {err}") from err


def sha256_drift_message(entry: dict[str, Any], data: bytes) -> str | None:
    """None when the download matches the pin, or nothing is pinned."""
    pinned_sha = entry.get("sha256")
    actual_sha = hashlib.sha256(data).hexdigest()
    if not pinned_sha or actual_sha == pinned_sha:
        return None
    return (
        f"{_display_name(entry)}: sha256 drift\n"
        f"        pinned {pinned_sha}\n"
        f"        actual {actual_sha}\n"
        f"        (store extensions are re-published frequently; "
        f"update extensions.yml after reviewing)"
    )


def unpack_extension(
    entry: dict[str, Any], data: bytes, *, out_root: Path
) -> dict[str, Any]:
    ext_id = entry["id"]
    try:
        return unpack_crx(data, out_root / ext_id, expected_id=ext_id)
    except Exception as err:
        raise ExtensionError(f"{_display_name(entry)}: {err}") from err


def format_ok_line(entry: dict[str, Any], info: dict[str, Any]) -> str:
    pinned_ver = entry.get("version")
    ver_note = ""
    if pinned_ver and info["version"] != pinned_ver:
        ver_note = f"  (pinned {pinned_ver})"
    return (
        f"  OK  {_display_name(entry):32} {entry['id']}  v{info['version']}{ver_note}"
    )


def _parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", default=str(repo_root / "src/exc_challenge/challenge/extensions.yml"))
    ap.add_argument("--lock", help="default: extensions.lock.yml beside --pool")
    ap.add_argument("--out", required=True, help="output dir, e.g. /opt/extensions")
    ap.add_argument(
        "--allow-sha-mismatch",
        action="store_true",
        help="warn instead of failing when the .crx hash has drifted "
        "(store extensions are re-published often; use only when refreshing pins)",
    )
    return ap.parse_args()


def main() -> int:
    args = _parse_args()

    spec = yaml.safe_load(Path(args.pool).read_text())
    cft_version = spec.get("chrome_for_testing_version", _DEFAULT_CFT_VERSION)
    names = [e["name"] for e in spec.get("pool") or []]
    if not names:
        print("ERROR: extensions.yml has an empty pool", file=sys.stderr)
        return 1

    lock_path = Path(args.lock) if args.lock else Path(args.pool).with_name("extensions.lock.yml")
    if not lock_path.is_file():
        print(f"ERROR: {lock_path} not found - it is private, not in the public "
              f"repository; use the published image", file=sys.stderr)
        return 1
    lock = yaml.safe_load(lock_path.read_text()) or {}
    missing = [n for n in names if not (lock.get(n) or {}).get("id")]
    if missing:
        print(f"ERROR: {lock_path} has no id for: {missing}", file=sys.stderr)
        return 1
    pool = [{"name": n, **lock[n]} for n in names]

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    drifted: list[str] = []

    for entry in pool:
        try:
            check_id_has_no_comma(entry)
            data = download_crx(entry, cft_version=cft_version)

            drift = sha256_drift_message(entry, data)
            if drift and not args.allow_sha_mismatch:
                raise ExtensionError(drift)
            if drift:
                drifted.append(drift)

            info = unpack_extension(entry, data, out_root=out_root)
        except ExtensionError as err:
            failures.append(str(err))
            continue

        print(format_ok_line(entry, info))

    for msg in drifted:
        print(f"  WARN {msg}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} extension(s) FAILED:", file=sys.stderr)
        for msg in failures:
            print(f"  FAIL {msg}", file=sys.stderr)
        return 1

    print(f"\n{len(pool)} extension(s) unpacked into {out_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
