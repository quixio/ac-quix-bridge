# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Create or update the Post-Race Analyzer agent in Quix.AI.

Reads .env for:
  POST_RACE_SUMMARY_KB_ID (single topical KB created via create_kb.py;
                           multiple reference files live as resources inside)

Writes:
  QUIX_AI_POST_RACE_AGENT_ID

Agent endpoint /ai/api/org/agents/{id} is verified via
quix-ai-exploration/probes/update_agent.py. Headers require x-version: 2.0.

NOTE: toolFilter intentionally omitted on the initial agent body. Quix.AI's
`mcp__<slug>__<tool>` slug = the MCP server's GUID (OrgMcpController.cs:79),
not the displayName. We don't have those GUIDs until register_mcp.py runs,
so leaving toolFilter unset gives the agent access to all org tools by
default. Tighten to a real whitelist after register_mcp returns the server
id — see TODO at the bottom of this file.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import httpx

from _common import portal, read_env_value, token, write_env


DISPLAY_NAME = "Post-Race Analyzer"


def _agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request body and skip the POST/PUT call",
    )
    args = parser.parse_args(argv)

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    system_prompt_path = repo_root / "post-race" / "system_prompt.md"
    system_prompt = system_prompt_path.read_text()

    post_race_kb = read_env_value("POST_RACE_SUMMARY_KB_ID") or os.environ.get(
        "POST_RACE_SUMMARY_KB_ID"
    )
    ac_kb = os.environ.get("AC_TELEMETRY_KB_ID")  # existing KB, set in shell env

    if not post_race_kb:
        print(
            "Missing POST_RACE_SUMMARY_KB_ID in .env. "
            "Run `uv run create_kb.py --title 'Post Race Summary'` first, "
            "then `uv run update_kb.py ../post-race/kb/*.md`."
        )
        return 1

    # accessLevel values per Quix.AI.Domain/AgentConfigurations/KbAccessLevel.cs:
    #   "summary" — snippet only / "standard" — full KB content injected.
    # "read" is NOT a valid value (server-side Enum.Parse will 400).
    kb_rules = [
        {"knowledgeBaseId": post_race_kb, "accessLevel": "standard"},
    ]
    if ac_kb:
        kb_rules.append({"knowledgeBaseId": ac_kb, "accessLevel": "standard"})

    body = {
        "displayName": DISPLAY_NAME,
        "systemPrompt": system_prompt,
        "kbAccessRules": kb_rules,
        # toolFilter omitted — see module docstring.
    }

    if args.dry_run:
        print("[dry-run] would POST/PUT body:")
        print(json.dumps(body, indent=2))
        return 0

    with httpx.Client(base_url=portal(), headers=_agent_headers(), timeout=60.0) as client:
        existing = client.get("/ai/api/org/agents").json()
        match = next((a for a in existing if a.get("displayName") == DISPLAY_NAME), None)
        if match:
            agent_id = match["id"]
            print(f"Updating existing agent {agent_id} ({DISPLAY_NAME})")
            client.put(f"/ai/api/org/agents/{agent_id}", json=body).raise_for_status()
        else:
            print(f"Creating new agent ({DISPLAY_NAME})")
            agent_id = client.post("/ai/api/org/agents", json=body).json()["id"]
            print(f"  id={agent_id}")

    write_env("QUIX_AI_POST_RACE_AGENT_ID", agent_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# TODO: tighten with real toolFilter once both MCP server GUIDs are known.
# After register_mcp.py runs we know TESTMANAGER_MCP_SERVER_ID. Quix.AI's
# QuixLake MCP server GUID is fixed per workspace (currently
# "1e237216cb7b444fbef92ec7142453d4" in quixdev).
#
# Real tool names then are:
#   mcp__<test-manager-guid>__get_test
#   mcp__<test-manager-guid>__get_session
#   mcp__<test-manager-guid>__list_logbook
#   mcp__<test-manager-guid>__get_driver
#   mcp__<test-manager-guid>__get_device
#   mcp__<test-manager-guid>__get_environment
#   mcp__<test-manager-guid>__list_sessions_for_test
#   mcp__<test-manager-guid>__list_recent_sessions_for_driver
#   mcp__<test-manager-guid>__save_analysis
#   mcp__1e237216cb7b444fbef92ec7142453d4__run_query
#   mcp__1e237216cb7b444fbef92ec7142453d4__get_schema
#   mcp__1e237216cb7b444fbef92ec7142453d4__list_partitions
#   delegate_task
#
# Add `toolFilter: {"mode": "Whitelist", "toolNames": [...]}` to body once GUIDs known.
