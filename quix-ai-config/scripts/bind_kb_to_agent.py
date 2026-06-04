# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Idempotently bind a KB to an agent.

Usage:
    uv run bind_kb_to_agent.py <agent_id> <kb_id>
"""

from __future__ import annotations

import sys

import httpx

from _common import portal, token


def _agent_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def main(argv: list[str] | None = None) -> int:
    if not argv:
        argv = sys.argv[1:]
    if len(argv) != 2:
        print("Usage: bind_kb_to_agent.py <agent_id> <kb_id>")
        return 2

    agent_id, kb_id = argv

    with httpx.Client(base_url=portal(), headers=_agent_headers(), timeout=60.0) as client:
        agent = client.get(f"/ai/api/org/agents/{agent_id}").json()
        rules = agent.get("kbAccessRules", [])
        if any(r["knowledgeBaseId"] == kb_id for r in rules):
            print(f"Agent {agent_id} already has KB {kb_id} bound.")
            return 0
        rules.append({"knowledgeBaseId": kb_id, "accessLevel": "read"})
        # PUT is full-document replace on this API; rebuild the body from the
        # existing agent doc with only kbAccessRules swapped so we don't wipe
        # systemPrompt / toolFilter / displayName.
        body = {**agent, "kbAccessRules": rules}
        # Strip read-only/server-managed fields that the API rejects on PUT.
        for k in ("id", "createdAt", "updatedAt", "version"):
            body.pop(k, None)
        client.put(
            f"/ai/api/org/agents/{agent_id}", json=body
        ).raise_for_status()
        print(f"Bound KB {kb_id} to agent {agent_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
