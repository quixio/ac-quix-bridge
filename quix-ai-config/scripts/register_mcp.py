"""Register an MCP server in the Quix.AI org config.

Usage:
    python register_mcp.py \
        --name test-manager \
        --display-name "Test Manager" \
        --url https://test-manager-backend-...quix.io/mcp \
        --api-key <generated>

Note: the MCP-server endpoint paths (/api/user/mcp-servers) are unverified —
the Quix.AI MCP-server admin API may live under a different prefix in this
deployment. If `register_mcp.py` 404s, fall back to registering the server
via the Quix Portal UI and stashing the resulting server_id manually:

    echo "TESTMANAGER_MCP_SERVER_ID=<id>" >> quix-ai-config/.env
"""

from __future__ import annotations

import argparse
import sys

from _common import http_client, write_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="slug — tools become mcp__<name>__<tool>")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--url", required=True, help="public URL of MCP endpoint")
    parser.add_argument("--api-key", required=True, help="shared X-API-Key secret")
    args = parser.parse_args(argv)

    with http_client() as client:
        # First, see if a server with this name already exists.
        existing = client.get("/api/user/mcp-servers").json()
        match = next((s for s in existing if s.get("name") == args.name), None)

        body = {
            "name": args.name,
            "displayName": args.display_name,
            "url": args.url,
            "auth": {
                "type": "api_key",
                "headerName": "X-API-Key",
                "credential": args.api_key,
            },
        }

        if match:
            server_id = match["id"]
            print(f"Updating existing MCP server {server_id} (name={args.name})")
            resp = client.put(f"/api/user/mcp-servers/{server_id}", json=body)
        else:
            print(f"Creating new MCP server (name={args.name})")
            resp = client.post("/api/user/mcp-servers", json=body)

        resp.raise_for_status()
        server_id = resp.json()["id"]

    write_env("TESTMANAGER_MCP_SERVER_ID", server_id)
    print("\nDone. API key (set in test-manager-backend env as TESTMANAGER_MCP_API_KEY):")
    print(f"  {args.api_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
