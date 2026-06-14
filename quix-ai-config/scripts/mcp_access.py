# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Show or change which agents may use an org MCP server.

Quix.AI gates MCP tools per agent: an agent sees a server's tools only if it's
on that server's agent-access allowlist (a silent gate — without it the agent
just never calls the tools). The PUT endpoint REPLACES the whole allowlist, so
--grant / --revoke here GET the current set first and merge, never clobbering
other agents' access.

Resolve the server by id or by a case-insensitive substring of its displayName.

Usage:
    uv run mcp_access.py --server Lakehouse                       # show access
    uv run mcp_access.py --server "Test Manager" --grant <agent-id>
    uv run mcp_access.py --server 97b2e5ec... --grant <id> --revoke <other-id>
"""

from __future__ import annotations

import argparse
import sys

from _common import active_env, http_client, portal


def _resolve_server(client, ref: str) -> dict:
    servers = client.get("/ai/api/org/mcp-servers").json()
    exact = next((s for s in servers if s.get("id") == ref), None)
    if exact:
        return exact
    matches = [
        s for s in servers if ref.lower() in (s.get("displayName") or "").lower()
    ]
    if not matches:
        names = ", ".join(repr(s.get("displayName")) for s in servers)
        raise SystemExit(f"no MCP server matching {ref!r}. Servers: {names}")
    if len(matches) > 1:
        names = ", ".join(repr(s.get("displayName")) for s in matches)
        raise SystemExit(f"{ref!r} is ambiguous — matches {names}. Use the id.")
    return matches[0]


def _print_access(access: list[dict]) -> None:
    for a in sorted(access, key=lambda x: not x.get("hasAccess")):
        mark = "✓" if a.get("hasAccess") else "·"
        print(f"  {mark} {a.get('agentDisplayName')}  ({a.get('agentId')})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server", required=True, help="MCP server id or displayName substring"
    )
    parser.add_argument("--grant", action="append", default=[], metavar="AGENT_ID")
    parser.add_argument("--revoke", action="append", default=[], metavar="AGENT_ID")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the merged set, don't PUT"
    )
    args = parser.parse_args(argv)

    print(f"[env: {active_env()}] target portal {portal()}")
    with http_client() as client:
        server = _resolve_server(client, args.server)
        sid, name = server["id"], server.get("displayName")
        print(f"Server: {name} ({sid})")

        access = client.get(f"/ai/api/org/mcp-servers/{sid}/agent-access").json()
        allowed = {a["agentId"] for a in access if a.get("hasAccess")}
        print("Current access:")
        _print_access(access)

        if not args.grant and not args.revoke:
            return 0  # show-only

        merged = (allowed | set(args.grant)) - set(args.revoke)
        if merged == allowed:
            print("No change — allowlist already matches.")
            return 0

        print(f"New allowlist ({len(merged)}): {sorted(merged)}")
        if args.dry_run:
            print("[dry-run] skipping PUT")
            return 0

        client.put(
            f"/ai/api/org/mcp-servers/{sid}/agent-access",
            json={"agentIds": sorted(merged)},
        ).raise_for_status()
        print("Updated. New access:")
        _print_access(client.get(f"/ai/api/org/mcp-servers/{sid}/agent-access").json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
