# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""List all org MCP servers."""

from __future__ import annotations

import json

import httpx

from _common import portal, token


def _mcp_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def main() -> int:
    with httpx.Client(base_url=portal(), headers=_mcp_headers(), timeout=60.0) as client:
        servers = client.get("/ai/api/org/mcp-servers").json()
    print(json.dumps(servers, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
