"""Create or update the Post-Race Analyzer agent in Quix.AI.

Reads .env for:
  ANALYSIS_CONTRACT_KB_ID
  TM_SCHEMA_KB_ID
  TESTMANAGER_MCP_SERVER_ID (informational — used to allowlist on the MCP server side)

Writes:
  QUIX_AI_POST_RACE_AGENT_ID

Agent endpoint /ai/api/org/agents/{id} is verified via
quix-ai-exploration/probes/update_agent.py. Headers require x-version: 2.0.
"""

from __future__ import annotations

import os
import pathlib
import sys

import httpx

from _common import portal, read_env_value, token, write_env


DISPLAY_NAME = "Post-Race Analyzer"

# Tool filter: explicit allowlist. Confirm exact `mcp__quixlake__*` tool names
# during impl by running `python list_agents.py` to inspect a working agent.
TOOL_FILTER_TOOL_NAMES = [
    "delegate_task",
    # quixlake-mcp — confirm exact server slug + tool names at install time
    "mcp__quixlake__sql",
    "mcp__quixlake__describe_table",
    # our test-manager-mcp tools (slug = "test-manager")
    "mcp__test-manager__get_test",
    "mcp__test-manager__get_session",
    "mcp__test-manager__list_logbook",
    "mcp__test-manager__get_driver",
    "mcp__test-manager__get_device",
    "mcp__test-manager__get_environment",
    "mcp__test-manager__list_sessions_for_test",
    "mcp__test-manager__list_recent_sessions_for_driver",
    "mcp__test-manager__save_analysis",
]


def _agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def main(argv: list[str] | None = None) -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    system_prompt_path = repo_root / "post-race" / "system_prompt.md"
    system_prompt = system_prompt_path.read_text()

    analysis_kb = read_env_value("ANALYSIS_CONTRACT_KB_ID") or os.environ.get("ANALYSIS_CONTRACT_KB_ID")
    tm_schema_kb = read_env_value("TM_SCHEMA_KB_ID") or os.environ.get("TM_SCHEMA_KB_ID")
    ac_kb = os.environ.get("AC_TELEMETRY_KB_ID")  # existing KB, set in shell env

    if not analysis_kb or not tm_schema_kb:
        print("Missing KB IDs in .env. Run update_kb_resource.py first.")
        return 1

    kb_rules = [
        {"knowledgeBaseId": analysis_kb, "accessLevel": "read"},
        {"knowledgeBaseId": tm_schema_kb, "accessLevel": "read"},
    ]
    if ac_kb:
        kb_rules.append({"knowledgeBaseId": ac_kb, "accessLevel": "read"})

    body = {
        "displayName": DISPLAY_NAME,
        "systemPrompt": system_prompt,
        "kbAccessRules": kb_rules,
        "toolFilter": {
            "mode": "whitelist",
            "toolNames": TOOL_FILTER_TOOL_NAMES,
        },
    }

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

    write_env("QUIX_AI_POST_RACE_AGENT_ID", agent_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
