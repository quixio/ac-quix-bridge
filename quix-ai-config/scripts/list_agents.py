# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""List all org agents."""

from __future__ import annotations

import json

import httpx

from _common import portal, token


def _agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def main() -> int:
    with httpx.Client(base_url=portal(), headers=_agent_headers(), timeout=60.0) as client:
        agents = client.get("/ai/api/org/agents").json()
    print(json.dumps(agents, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
