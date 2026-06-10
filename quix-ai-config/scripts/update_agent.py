# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Create or update a Quix.AI agent.

Reads `<agent>/system_prompt.md` and per-agent metadata from the `AGENTS` map
below. Matches the existing agent by `displayName`; PUTs if found, POSTs if not.
Writes the resolved agent ID back to .env under the configured key.

Usage:
    uv run update_agent.py --agent post-race
    uv run update_agent.py --agent quixlake-querier
    uv run update_agent.py --agent post-race --dry-run

Agents:
  post-race          — Post-Race Analyzer (one analysis report per session)
  quixlake-querier   — QuixLake Querier (chat in Telemetry Explorer)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import httpx

from _common import portal, read_env_value, token, write_env


# Per-agent metadata. To onboard a new agent: create a sibling folder with
# system_prompt.md (+ optional kb/ resources) and add an entry here.
AGENTS: dict[str, dict] = {
    "post-race": {
        "display_name": "Post-Race Analyzer",
        "output_env": "QUIX_AI_POST_RACE_AGENT_ID",
        # KB rules built fresh from env vars on every run.
        "kb_rules_from_env": [
            {
                "env_var": "POST_RACE_SUMMARY_KB_ID",
                "access_level": "standard",
                "required": True,
            },
            {
                "env_var": "AC_TELEMETRY_KB_ID",
                "access_level": "standard",
                "required": False,
            },
        ],
        "preserve_existing_kb_rules": False,
    },
    "quixlake-querier": {
        "display_name": "QuixLake Querier",
        "output_env": "QUIX_AI_QUIXLAKE_QUERIER_AGENT_ID",
        "kb_rules_from_env": [
            {
                "env_var": "AC_TELEMETRY_KB_ID",
                "access_level": "standard",
                "required": True,
            },
        ],
        "preserve_existing_kb_rules": False,
    },
}


def _agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def _build_kb_rules(config: dict) -> list[dict] | None:
    """Return new KB rules from env, or None when the agent preserves existing rules."""
    if config["preserve_existing_kb_rules"]:
        return None
    rules: list[dict] = []
    for rule in config["kb_rules_from_env"]:
        env_var = rule["env_var"]
        kb_id = read_env_value(env_var) or os.environ.get(env_var)
        if not kb_id:
            if rule["required"]:
                print(
                    f"Missing required KB env var {env_var!r}. "
                    "Run `uv run create_kb.py` first."
                )
                sys.exit(1)
            continue
        rules.append({"knowledgeBaseId": kb_id, "accessLevel": rule["access_level"]})
    return rules


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--agent",
        required=True,
        choices=sorted(AGENTS),
        help="Which agent folder to use",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request body and skip the POST/PUT call",
    )
    parser.add_argument(
        "--agent-id",
        default=None,
        help=(
            "Target this agent id directly instead of matching by display name. "
            "Preserves the target's own name + existing KB rules and pushes only "
            "the prompt — bind KBs separately with bind_kb_to_agent.py."
        ),
    )
    args = parser.parse_args(argv)

    config = AGENTS[args.agent]
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    system_prompt_path = repo_root / args.agent / "system_prompt.md"
    system_prompt = system_prompt_path.read_text()

    display_name = config["display_name"]

    with httpx.Client(
        base_url=portal(), headers=_agent_headers(), timeout=60.0
    ) as client:
        existing = client.get("/ai/api/org/agents").json()
        if args.agent_id:
            match = next((a for a in existing if a.get("id") == args.agent_id), None)
            if not match:
                raise SystemExit(f"agent id {args.agent_id} not found")
            # Target by id: preserve the agent's own name + existing KB rules,
            # push only the prompt. Bind KBs separately (bind_kb_to_agent.py).
            display_name = match["displayName"]
            body: dict = {"displayName": display_name, "systemPrompt": system_prompt}
            body["kbAccessRules"] = match.get("kbAccessRules", [])
        else:
            match = next(
                (a for a in existing if a.get("displayName") == display_name), None
            )
            body = {"displayName": display_name, "systemPrompt": system_prompt}
            kb_rules = _build_kb_rules(config)
            if kb_rules is not None:
                body["kbAccessRules"] = kb_rules
            elif match:
                # preserve_existing_kb_rules=True path: carry over what's deployed.
                body["kbAccessRules"] = match.get("kbAccessRules", [])

        if args.dry_run:
            print("[dry-run] would POST/PUT body:")
            print(json.dumps(body, indent=2))
            return 0

        if match:
            agent_id = match["id"]
            print(f"Updating existing agent {agent_id} ({display_name})")
            client.put(
                f"/ai/api/org/agents/{agent_id}", json=body
            ).raise_for_status()
        else:
            print(f"Creating new agent ({display_name})")
            agent_id = client.post("/ai/api/org/agents", json=body).json()["id"]
            print(f"  id={agent_id}")

    write_env(config["output_env"], agent_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# TODO: tighten with real toolFilter once both MCP server GUIDs are known.
# Quix.AI's `mcp__<slug>__<tool>` slug = the MCP server's GUID (verified via
# OrgMcpController.cs:79), not the displayName. After register_mcp.py runs we
# know TESTMANAGER_MCP_SERVER_ID. Quix.AI's QuixLake MCP server GUID is fixed
# per workspace (currently "1e237216cb7b444fbef92ec7142453d4" in quixdev).
#
# Add `body["toolFilter"] = {"mode": "Whitelist", "toolNames": [...]}`
# once GUIDs are known. Apply only to post-race; quixlake-querier preserves
# whatever is set in the Portal UI.
