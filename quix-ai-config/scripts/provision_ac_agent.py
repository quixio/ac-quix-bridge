"""Provision the AC Telemetry Agent on a Quix org via quix-ai-sdk.

Idempotent: KB (+ both resource files), the Telemetry Explorer MCP server
(plot_data), the agent itself, and the agent's MCP access are all reconciled
with the SDK's ensure_* functions.

Env (org-admin PAT): Quix__Portal__Api, QUIX_TOKEN. Optional
TELEMETRY_COMPARISON_MCP_API_KEY — when unset a fresh key is generated and
copied to the clipboard (never printed); paste it into the project variable of
the same name before the plot_data tool will work.

Usage:
    op run --env-file=<env> -- uv run --with-editable ~/repos/quix-ai-sdk \
        python provision_ac_agent.py --te-url https://telemetryexplorer-<ws>.<domain> [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

from quix_ai_sdk import agents, knowledge, mcp

logger = logging.getLogger("provision_ac_agent")

AGENT_DIR = Path(__file__).resolve().parent.parent / "ac-telemetry-agent"
SYSTEM_PROMPT = AGENT_DIR / "system_prompt.md"
KB_FILES = (
    AGENT_DIR / "kb" / "kb_ac_channels.md",
    AGENT_DIR / "kb" / "kb_ac_telemetry_patterns.md",
)

AGENT_NAME = "ac-telemetry-agent"
AGENT_DISPLAY = "AC Telemetry Agent"
AGENT_DESC = "Queries AC/ACC lap telemetry in the lakehouse and plots traces in Telemetry Explorer."
KB_TITLE = "AC Telemetry"
KB_DESC = "AC channel list + telemetry query patterns for the AC Telemetry Agent."
MCP_DISPLAY = "Telemetry Explorer"
MCP_KEY_ENV = "TELEMETRY_COMPARISON_MCP_API_KEY"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--te-url",
        required=True,
        help="Public base URL of the Telemetry Explorer deployment",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes, write nothing"
    )
    return parser.parse_args()


def _mcp_api_key() -> str:
    key = os.environ.get(MCP_KEY_ENV, "").strip()
    if key and key != "placeholder":
        logger.info("using %s from env", MCP_KEY_ENV)
        return key
    key = secrets.token_hex(32)
    subprocess.run(["pbcopy"], input=key.encode(), check=True)
    logger.warning(
        "generated new MCP API key -> clipboard; set project var %s to it", MCP_KEY_ENV
    )
    return key


def ensure_kb(dry_run: bool) -> str | None:
    kb = knowledge.ensure(KB_TITLE, KB_DESC, dry_run=dry_run)
    if not isinstance(kb, str):
        logger.info("%s", kb)
        return None
    logger.info("knowledge-base %s: ok", kb)
    # Fire-and-forget: processing runs server-side while MCP + agent are created.
    for path in KB_FILES:
        logger.info(
            "%s", knowledge.sync_resource(kb, path, wait=False, dry_run=dry_run)
        )
    return kb


def ensure_mcp(te_url: str, dry_run: bool) -> str | None:
    spec = mcp.McpSpec(
        display_name=MCP_DISPLAY,
        url=f"{te_url.rstrip('/')}/mcp/",  # trailing slash: /mcp 307s to http
        api_key=_mcp_api_key(),
    )
    result = mcp.ensure(spec, dry_run=dry_run)
    if not isinstance(result, str):
        logger.info("%s", result)
        return None
    logger.info("mcp-server %s: ok (%s)", result, spec.url)
    return result


def ensure_agent(kb_id: str | None, dry_run: bool) -> str | None:
    spec = agents.AgentSpec(
        name=AGENT_NAME,
        display_name=AGENT_DISPLAY,
        system_prompt=SYSTEM_PROMPT,
        kb_ids=(kb_id,) if kb_id else (),
        description=AGENT_DESC,
    )
    result = agents.ensure(spec, dry_run=dry_run)
    logger.info("%s", result)
    return result.id


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] [%(name)s] %(message)s",
    )
    args = parse_args()
    logger.info(
        "prompt %s: %d chars",
        SYSTEM_PROMPT.name,
        agents.system_prompt_length(SYSTEM_PROMPT.read_text()),
    )

    kb_id = ensure_kb(args.dry_run)
    server_id = ensure_mcp(args.te_url, args.dry_run)
    agent_id = ensure_agent(kb_id, args.dry_run)
    if server_id and agent_id:
        logger.info(
            "%s", mcp.ensure_access(server_id, include=[agent_id], dry_run=args.dry_run)
        )

    print(f"AC_TELEMETRY_AGENT_ID={agent_id or '<dry-run>'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
