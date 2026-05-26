# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Register an MCP server in the Quix.AI org config and (optionally) grant
agent access to it.

Endpoint: POST /ai/api/org/mcp-servers (verified via list_mcp_servers probe).
Real schema fields per `Quix.AI.Service/Controllers/OrgMcpController.cs`:
  displayName, url, authType ("api_key" | "none"), credential, enabled,
  toolAllowlist, rateLimitPerMinute.

The MCP server's slug used in tool names (`mcp__<slug>__<tool>`) is the
server's GUID — NOT the displayName. Don't bother passing a `name` arg.

Usage:
    uv run register_mcp.py \
        --display-name "Test Manager" \
        --url https://abc123.ngrok-free.app/mcp \
        --api-key "$(openssl rand -hex 32)" \
        [--agent-id <id>]    # if provided, also grants this agent access

Writes:
    TESTMANAGER_MCP_SERVER_ID=<id>  to quix-ai-config/.env
Prints the api-key for the operator to paste into TM backend env.
"""

from __future__ import annotations

import argparse
import sys

from _common import http_client, write_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--url", required=True, help="public URL of MCP endpoint")
    parser.add_argument("--api-key", required=True, help="shared X-API-Key secret")
    parser.add_argument(
        "--agent-id",
        default=None,
        help="Optional agent ID to grant access to this server after creation",
    )
    args = parser.parse_args(argv)

    body = {
        "displayName": args.display_name,
        "url": args.url,
        "authType": "api_key",
        "credential": args.api_key,
        "enabled": True,
        "rateLimitPerMinute": 100,
        "toolAllowlist": [],
    }

    with http_client() as client:
        # Look for an existing server with the same displayName + url.
        existing = client.get("/ai/api/org/mcp-servers").json()
        match = next(
            (s for s in existing if s.get("displayName") == args.display_name),
            None,
        )

        if match:
            server_id = match["id"]
            print(f"Updating existing MCP server {server_id} ({args.display_name})")
            resp = client.put(f"/ai/api/org/mcp-servers/{server_id}", json=body)
        else:
            print(f"Creating new MCP server ({args.display_name})")
            resp = client.post("/ai/api/org/mcp-servers", json=body)

        resp.raise_for_status()
        server_id = resp.json()["id"]
        print(f"  server_id={server_id}")

        if args.agent_id:
            print(
                f"Granting agent {args.agent_id} access to server {server_id}"
            )
            access = client.put(
                f"/ai/api/org/mcp-servers/{server_id}/agent-access",
                json={"agentIds": [args.agent_id]},
            )
            if access.is_error:
                # Not blocking — operator can do this via Portal UI instead.
                print(
                    f"  WARN agent-access call failed: {access.status_code} "
                    f"{access.text[:200]}"
                )
            else:
                print("  agent-access granted")

    write_env("TESTMANAGER_MCP_SERVER_ID", server_id)
    print(
        "\nDone. API key (set in TM backend env as TESTMANAGER_MCP_API_KEY):"
    )
    print(f"  {args.api_key}")
    print(
        f"\nTool names available to agents: mcp__{server_id}__<tool>\n"
        "Use these literal strings in any toolFilter whitelist later."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
