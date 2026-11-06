"""Provision one of this repo's Quix.AI agents on an org via quix-ai-sdk.

Idempotent: the agent's KB (+ resource files), its MCP server, the agent
itself, and the agent's MCP access are reconciled with the SDK's ensure_*
functions. KB processing is fire-and-forget (runs server-side while the rest
is created).

Env (org-admin PAT): Quix__Portal__Api, QUIX_TOKEN. The agent's MCP key env
var (see AGENTS) is used when set; otherwise a fresh key is generated and
copied to the clipboard (never printed) — paste it into the project variable
of the same name.

Usage:
    op run --env-file=<env> -- uv run --with-editable ~/repos/quix-ai-sdk --with httpx \
        python provision_agent.py --agent ac-telemetry --mcp-url https://<deployment-public-url> [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from quix_ai_sdk import agents, knowledge, mcp
from quix_ai_sdk.errors import QuixAIError

logger = logging.getLogger("provision_agent")

CONFIG_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AgentConfig:
    """Everything needed to reconcile one agent; prompt + KB files live under `folder`."""

    folder: str
    name: str
    display_name: str
    description: str
    kb_title: str
    kb_description: str
    kb_files: tuple[str, ...]
    mcp_display: str
    mcp_key_env: str
    output_var: str
    extra_kb_titles: tuple[str, ...] = field(default_factory=tuple)


AGENTS: dict[str, AgentConfig] = {
    "ac-telemetry": AgentConfig(
        folder="ac-telemetry-agent",
        name="ac-telemetry-agent",
        display_name="AC Telemetry Agent",
        description="Queries AC/ACC lap telemetry in the lakehouse and plots traces in Telemetry Explorer.",
        kb_title="AC Telemetry",
        kb_description="AC channel list + telemetry query patterns for the AC Telemetry Agent.",
        kb_files=("kb/kb_ac_channels.md", "kb/kb_ac_telemetry_patterns.md"),
        mcp_display="Telemetry Explorer",
        mcp_key_env="TELEMETRY_COMPARISON_MCP_API_KEY",
        output_var="AC_TELEMETRY_AGENT_ID",
    ),
    "post-race": AgentConfig(
        folder="post-race",
        name="post-race-analyzer",
        display_name="Post-Race Analyzer",
        description="Headless post-race analysis: queries the lake, checks requirements, saves the report to Test Manager.",
        kb_title="Post Race Summary",
        kb_description="SaveAnalysisPayload contract + Test Manager schema for the Post-Race Analyzer.",
        kb_files=("kb/analysis_contract.md", "kb/tm_schema.md"),
        mcp_display="Test Manager",
        mcp_key_env="TESTMANAGER_MCP_API_KEY",
        output_var="POST_RACE_AGENT_ID",
        extra_kb_titles=("AC Telemetry",),
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", required=True, choices=sorted(AGENTS))
    parser.add_argument(
        "--mcp-url",
        required=True,
        help="Public base URL of the deployment serving this agent's /mcp",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Report planned changes, write nothing"
    )
    return parser.parse_args()


def _mcp_api_key(env_var: str) -> str:
    key = os.environ.get(env_var, "").strip()
    if key and key != "placeholder":
        logger.info("using %s from env", env_var)
        return key
    key = secrets.token_hex(32)
    subprocess.run(["pbcopy"], input=key.encode(), check=True)
    logger.warning(
        "generated new MCP API key -> clipboard; set project var %s to it", env_var
    )
    return key


def ensure_kb(cfg: AgentConfig, dry_run: bool) -> tuple[str | None, bool]:
    """Reconcile the KB + files. Returns (kb_id, needs_reprocess).

    Processing is fire-and-forget so MCP + agent creation overlap it. A second
    file's trigger 400s while the first is still running; the upload itself has
    already landed, so we flag a final re-process instead of failing.
    """
    kb = knowledge.ensure(cfg.kb_title, cfg.kb_description, dry_run=dry_run)
    if not isinstance(kb, str):
        logger.info("%s", kb)
        return None, False
    logger.info("knowledge-base %s: ok", kb)
    needs_reprocess = False
    for rel in cfg.kb_files:
        path = CONFIG_ROOT / cfg.folder / rel
        try:
            logger.info(
                "%s", knowledge.sync_resource(kb, path, wait=False, dry_run=dry_run)
            )
        except QuixAIError as exc:
            if "/process failed" not in str(exc):
                raise
            logger.warning("kb %s: %s uploaded; process busy, will re-run", kb, rel)
            needs_reprocess = True
    return kb, needs_reprocess


def reprocess_when_idle(kb_id: str, poll_s: float = 5.0, max_polls: int = 120) -> None:
    """Wait for the running KB processing to finish, then trigger one more pass."""
    for _ in range(max_polls):
        status = knowledge.processing_status(kb_id).get("processingStatus")
        if status != "processing":
            break
        time.sleep(poll_s)
    logger.info("kb %s: idle (%s), re-processing", kb_id, status)
    knowledge.process(kb_id, wait=True)
    logger.info(
        "kb %s: %s", kb_id, knowledge.processing_status(kb_id).get("processingStatus")
    )


def _existing_kb_ids(titles: tuple[str, ...]) -> list[str]:
    """Resolve already-provisioned KBs by title; missing ones are logged and skipped."""
    if not titles:
        return []
    by_title = {
        str(kb.get("title") or "").casefold(): kb.get("id") or kb.get("knowledgeBaseId")
        for kb in knowledge.list()
    }
    found: list[str] = []
    for title in titles:
        kb_id = by_title.get(title.casefold())
        if kb_id:
            found.append(kb_id)
        else:
            logger.warning("extra KB %r not found — provision its agent first", title)
    return found


def ensure_mcp(cfg: AgentConfig, base_url: str, dry_run: bool) -> str | None:
    spec = mcp.McpSpec(
        display_name=cfg.mcp_display,
        url=f"{base_url.rstrip('/')}/mcp/",  # trailing slash: /mcp 307s to http
        api_key=_mcp_api_key(cfg.mcp_key_env),
    )
    result = mcp.ensure(spec, dry_run=dry_run)
    if not isinstance(result, str):
        logger.info("%s", result)
        return None
    logger.info("mcp-server %s: ok (%s)", result, spec.url)
    return result


def ensure_agent(cfg: AgentConfig, kb_ids: list[str], dry_run: bool) -> str | None:
    spec = agents.AgentSpec(
        name=cfg.name,
        display_name=cfg.display_name,
        system_prompt=CONFIG_ROOT / cfg.folder / "system_prompt.md",
        kb_ids=tuple(kb_ids),
        description=cfg.description,
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
    cfg = AGENTS[args.agent]
    prompt = CONFIG_ROOT / cfg.folder / "system_prompt.md"
    logger.info(
        "agent %s: prompt %d chars",
        cfg.name,
        agents.system_prompt_length(prompt.read_text()),
    )

    kb_ids = _existing_kb_ids(cfg.extra_kb_titles)
    own_kb, needs_reprocess = ensure_kb(cfg, args.dry_run)
    if own_kb:
        kb_ids.insert(0, own_kb)
    server_id = ensure_mcp(cfg, args.mcp_url, args.dry_run)
    agent_id = ensure_agent(cfg, kb_ids, args.dry_run)
    if server_id and agent_id:
        logger.info(
            "%s", mcp.ensure_access(server_id, include=[agent_id], dry_run=args.dry_run)
        )
    if own_kb and needs_reprocess and not args.dry_run:
        reprocess_when_idle(own_kb)

    print(f"{cfg.output_var}={agent_id or '<dry-run>'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
