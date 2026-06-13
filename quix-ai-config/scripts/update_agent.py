# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Create or update a Quix.AI agent.

Reads `<agent>/system_prompt.md` and per-agent metadata from the `AGENTS` map
below. ID-first: resolves the target by the agent id stored in this env's .env
(the `output_env` key) or `--agent-id`; PUTs if found — pushing prompt +
`displayName`, so renames apply — else POSTs a new agent. Writes the agent id
back to the selected .env.

Usage:
    uv run update_agent.py --agent post-race
    uv run update_agent.py --agent ac-telemetry-agent
    uv run update_agent.py --agent post-race --dry-run

Agents:
  post-race          — Post-Race Analyzer (one analysis report per session)
  ac-telemetry-agent — AC Telemetry Agent (chat in Telemetry Explorer)
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

import httpx

from _common import active_env, ca_verify, portal, read_env_value, token, write_env


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
    "ac-telemetry-agent": {
        "display_name": "AC Telemetry Agent",
        "output_env": "QUIX_AI_AC_TELEMETRY_AGENT_ID",
        "kb_rules_from_env": [
            {
                "env_var": "AC_TELEMETRY_KB_ID",
                "access_level": "standard",
                "required": False,  # bind KBs later; agent can bootstrap without
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
            "Adopt an existing agent by id — a one-time seed when its id isn't "
            "yet stored in this env's .env. Thereafter the stored id drives updates."
        ),
    )
    args = parser.parse_args(argv)

    config = AGENTS[args.agent]
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    system_prompt_path = repo_root / args.agent / "system_prompt.md"
    system_prompt = system_prompt_path.read_text()

    display_name = config["display_name"]

    print(f"[env: {active_env()}] target portal {portal()}")

    # ID-first: operate on a known agent id (explicit flag wins, else the id
    # stashed in this env's .env). Only POST a new agent when we have none.
    target_id = args.agent_id or read_env_value(config["output_env"])

    with httpx.Client(
        base_url=portal(), headers=_agent_headers(), timeout=60.0, verify=ca_verify()
    ) as client:
        existing = client.get("/ai/api/org/agents").json()
        match = (
            next((a for a in existing if a.get("id") == target_id), None)
            if target_id
            else None
        )
        if target_id and not match:
            if args.agent_id:
                raise SystemExit(f"agent id {target_id} not found in {active_env()}")
            print(f"  stored id {target_id} not found in {active_env()} — creating fresh")

        # Always push the config displayName (so renames take) + the prompt.
        body: dict = {"displayName": display_name, "systemPrompt": system_prompt}
        kb_rules = _build_kb_rules(config)
        if kb_rules:  # explicit KB ids resolved → set them
            body["kbAccessRules"] = kb_rules
        elif match:  # updating → keep whatever's already bound
            body["kbAccessRules"] = match.get("kbAccessRules", [])
        else:  # fresh agent, no KBs yet
            body["kbAccessRules"] = []

        if args.dry_run:
            print(f"[dry-run] would {'PUT' if match else 'POST'} body:")
            print(json.dumps(body, indent=2))
            return 0

        if match:
            agent_id = match["id"]
            print(f"Updating agent {agent_id} ({display_name})")
            client.put(f"/ai/api/org/agents/{agent_id}", json=body).raise_for_status()
        else:
            print(f"Creating agent ({display_name})")
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
# once GUIDs are known. Apply only to post-race; ac-telemetry-agent preserves
# whatever is set in the Portal UI.
