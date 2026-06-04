"""Probe script — fires 6 HTTP requests against the live Lakehouse Query API.

Run from the telemetry-comparison directory so python-dotenv picks up .env:

    cd telemetry-comparison
    python scripts/probe_lakehouse.py

Outputs structured, labelled blocks that can be pasted verbatim into
spec.md §9 (## Probe results).
"""

from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("Quix__Lakehouse__Query__Url", "").rstrip("/")  # noqa: SIM112
TOKEN = os.getenv("Quix__Lakehouse__Query__AuthToken", "")  # noqa: SIM112

EXAMPLE_SQL = (
    "SELECT *\n"
    "FROM ac_telemetry\n"
    "WHERE environment = 'prague_office'"
    " AND test_rig = 'fanatec_csl_dd'"
    " AND experiment = 'TestDrive'"
    " AND driver = 'bob'"
    " AND track = 'Spa'"
    " AND carModel = 'lamborghini_huracan_gt3_evo'"
    " AND session_id = '2026-06-04T09:35:54.259Z'"
    " AND lap = 1\n"
    "LIMIT 100"
)

BAD_SQL = "SELECT FROM nope"

AUTH_HEADER = {"Authorization": f"Bearer {TOKEN}"}


def _print_block(label: str, status: int | None, ct: str, body_preview: str, byte_count: int) -> None:
    print(f"\n{'=' * 60}")
    print(f"[{label}]")
    print(f"  status       : {status}")
    print(f"  content-type : {ct}")
    print("  body (first 500 chars):")
    print(f"    {body_preview[:500]!r}")
    print(f"  byte count   : {byte_count}")


def _get(client: httpx.Client, label: str, url: str) -> None:
    try:
        r = client.get(url, headers=AUTH_HEADER)
        body = r.text
        _print_block(
            label,
            r.status_code,
            r.headers.get("content-type", ""),
            body,
            len(r.content),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n{'=' * 60}")
        print(f"[{label}] EXCEPTION: {exc}")


def _post(client: httpx.Client, label: str, url: str, sql: str, params: dict | None = None) -> None:
    try:
        r = client.post(
            url,
            content=sql.encode(),
            headers={**AUTH_HEADER, "Content-Type": "text/plain"},
            params=params or {},
        )
        body = r.text
        _print_block(
            label,
            r.status_code,
            r.headers.get("content-type", ""),
            body,
            len(r.content),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n{'=' * 60}")
        print(f"[{label}] EXCEPTION: {exc}")


def main() -> None:
    if not BASE_URL or not TOKEN:
        print("ERROR: Quix__Lakehouse__Query__Url or Quix__Lakehouse__Query__AuthToken not set.")
        sys.exit(1)

    print(f"Probing: {BASE_URL}")
    print(f"Token length: {len(TOKEN)} chars")

    with httpx.Client(timeout=30.0, follow_redirects=True, verify=False) as client:
        # 1. Swagger discovery
        _get(client, "1a GET /api/swagger", f"{BASE_URL}/api/swagger")
        _get(client, "1b GET /swagger", f"{BASE_URL}/swagger")

        # 2. POST /api/query?union_by_name=true — primary target
        _post(
            client,
            "2 POST /api/query?union_by_name=true (example SQL)",
            f"{BASE_URL}/api/query",
            EXAMPLE_SQL,
            {"union_by_name": "true"},
        )

        # 3. POST /query (legacy path, no /api prefix)
        _post(
            client,
            "3 POST /query (legacy, no /api prefix)",
            f"{BASE_URL}/query",
            EXAMPLE_SQL,
        )

        # 4. POST /api/query WITHOUT union_by_name
        _post(
            client,
            "4 POST /api/query (no union_by_name param)",
            f"{BASE_URL}/api/query",
            EXAMPLE_SQL,
        )

        # 5. POST /api/query with bad SQL — capture error shape
        _post(
            client,
            "5 POST /api/query?union_by_name=true (bad SQL error shape)",
            f"{BASE_URL}/api/query",
            BAD_SQL,
            {"union_by_name": "true"},
        )

        # 6. Metadata endpoints
        _get(client, "6a GET /api/tables", f"{BASE_URL}/api/tables")
        _get(client, "6b GET /api/schema?table=ac_telemetry", f"{BASE_URL}/api/schema?table=ac_telemetry")

    print(f"\n{'=' * 60}")
    print("Probe complete.")


if __name__ == "__main__":
    main()
