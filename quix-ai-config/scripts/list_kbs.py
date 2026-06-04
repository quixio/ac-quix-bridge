# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""List all org KBs."""

from __future__ import annotations

import json

from _common import http_client


def main() -> int:
    with http_client() as client:
        kbs = client.get("/api/org/knowledge-bases").json()
    print(json.dumps(kbs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
