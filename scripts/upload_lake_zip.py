"""Stream a telemetry-export zip into the lake-zip-loader service.

Each ``*.parquet`` entry in the zip is PUT to
``{url}/files/{prefix}/{entry_path}`` with the ``X-Api-Key`` header. The outer
three partitions (``environment``/``test_rig``/``experiment``) are NOT in the
zip entry paths — they are encoded in the zip filename and supplied as
``--prefix`` (auto-derived from the filename when omitted).

Uploads run through a small thread pool; each file is retried a few times with
linear backoff. The loader is idempotent, so re-running after a partial upload
only re-sends the files that failed (already-present files return "exists").

stdlib + httpx only — no QuixStreams dependency.

Example::

    python scripts/upload_lake_zip.py \\
        --zip "environment=prague_office_test_rig=fanatec_csl_dd_experiment=StuttgartExpo.zip" \\
        --url https://lake-zip-loader-<env>.<edge> \\
        --api-key stuttgart-expo-load-2026 --insecure
"""

from __future__ import annotations

import argparse
import re
import sys
import time
import urllib.parse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

# Partition keys encoded in the zip FILENAME (in order). The first has no
# leading separator; the rest are prefixed with "_" inside the filename.
PREFIX_KEYS = ("environment", "test_rig", "experiment")

RETRIES = 3
BACKOFF_S = 1.5
TIMEOUT_S = 120.0


def derive_prefix(zip_path: str) -> str:
    """Turn a zip filename stem into a Hive prefix.

    ``environment=prague_office_test_rig=fanatec_csl_dd_experiment=StuttgartExpo``
    -> ``environment=prague_office/test_rig=fanatec_csl_dd/experiment=StuttgartExpo``

    Splits only at ``_`` that immediately precedes one of the later partition
    keys, so underscores inside values (``prague_office``, ``fanatec_csl_dd``)
    are preserved.
    """
    stem = Path(zip_path).stem
    pattern = r"_(?=(?:" + "|".join(PREFIX_KEYS[1:]) + r")=)"
    parts = re.split(pattern, stem)
    return "/".join(parts)


def _put_entry(
    zip_path: str,
    entry: str,
    base_url: str,
    prefix: str,
    api_key: str,
    verify: bool,
) -> tuple[str, int, str, str]:
    """Read one zip entry and PUT it. Returns (status, nbytes, entry, error)."""
    with zipfile.ZipFile(zip_path) as zf:
        data = zf.read(entry)
    nbytes = len(data)

    relpath = f"{prefix}/{entry}" if prefix else entry
    # Keep "/" and "=" literal (structural); percent-encode spaces, colons, etc.
    encoded = urllib.parse.quote(relpath, safe="/=")
    put_url = f"{base_url}/files/{encoded}"
    headers = {"X-Api-Key": api_key, "Content-Type": "application/octet-stream"}

    last_err = ""
    for attempt in range(1, RETRIES + 1):
        try:
            with httpx.Client(verify=verify, timeout=TIMEOUT_S) as client:
                resp = client.put(put_url, content=data, headers=headers)
            if resp.status_code == 200:
                try:
                    status = resp.json().get("status", "ok")
                except ValueError:
                    status = "ok"
                return status, nbytes, entry, ""
            last_err = f"HTTP {resp.status_code}: {resp.text[:180]}"
        except Exception as exc:  # noqa: BLE001 - network/transport, retry
            last_err = f"{type(exc).__name__}: {exc}"
        if attempt < RETRIES:
            time.sleep(BACKOFF_S * attempt)

    return "failed", nbytes, entry, last_err


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream a telemetry export zip into the lake-zip-loader service."
    )
    parser.add_argument("--zip", required=True, help="Path to the export .zip")
    parser.add_argument(
        "--url", required=True, help="Base URL of the lake-zip-loader service"
    )
    parser.add_argument(
        "--api-key", required=True, help="X-Api-Key value (matches UPLOAD_API_KEY)"
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help=(
            "Partition prefix environment=.../test_rig=.../experiment=... "
            "(derived from the zip filename when omitted)"
        ),
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Concurrent upload threads (default 4)"
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification (self-signed edges)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.url.rstrip("/")
    prefix = args.prefix if args.prefix is not None else derive_prefix(args.zip)
    verify = not args.insecure

    with zipfile.ZipFile(args.zip) as zf:
        entries = [
            name
            for name in zf.namelist()
            if name.endswith(".parquet") and not name.endswith("/")
        ]
    total = len(entries)
    if total == 0:
        print("No .parquet entries found in zip", file=sys.stderr)
        return 1

    print(f"Uploading {total} parquet file(s) to {base_url}")
    print(f"Prefix: {prefix}")

    stored = existing = failed = 0
    total_bytes = 0
    done = 0
    start = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _put_entry, args.zip, entry, base_url, prefix, args.api_key, verify
            ): entry
            for entry in entries
        }
        for future in as_completed(futures):
            done += 1
            status, nbytes, entry, err = future.result()
            if status == "exists":
                existing += 1
                label = "exists"
            elif status in ("stored", "ok"):
                stored += 1
                total_bytes += nbytes
                label = "stored"
            else:
                failed += 1
                label = f"FAILED ({err})"
            print(f"[{done}/{total}] {label} {entry}")

    elapsed = time.time() - start
    print(
        f"\nDone: stored={stored} exists={existing} failed={failed} "
        f"({total_bytes / 1e6:.1f} MB in {elapsed:.1f}s)"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
