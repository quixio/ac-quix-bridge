# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""List recent Quix.AI chat sessions scoped to the PAT in QUIX_TOKEN.

Usage:
    uv run scripts/list_sessions.py                 # 20 most recent
    uv run scripts/list_sessions.py --limit 50
    uv run scripts/list_sessions.py --agent <id>    # only sessions on that agent
    uv run scripts/list_sessions.py --json          # raw JSON dump

The agent column is the agentConfigurationId the session is bound to. Pass
--agent to filter; with no value it defaults to QUIX_AI_POST_RACE_AGENT_ID
(falls back to the known Post-Race Analyzer id) so the common debug case is
a no-arg run.
"""

from __future__ import annotations

import argparse
import json
import os

import httpx

from _common import portal, token

POST_RACE_AGENT_ID = "350c788d-d25f-4aea-a78c-61ebab32b059"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def _activity(s: dict) -> str:
    return s.get("lastActivityAt") or s.get("updatedAt") or s.get("createdAt") or ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="max rows to show")
    parser.add_argument(
        "--agent",
        nargs="?",
        const=os.environ.get("QUIX_AI_POST_RACE_AGENT_ID", POST_RACE_AGENT_ID),
        default=None,
        help="filter to one agentConfigurationId (default: Post-Race Analyzer)",
    )
    parser.add_argument("--json", action="store_true", help="dump raw JSON")
    args = parser.parse_args()

    with httpx.Client(base_url=portal(), headers=_headers(), timeout=60.0) as client:
        r = client.get("/ai/api/sessions")
        r.raise_for_status()
        sessions = r.json()

    if args.agent:
        sessions = [
            s for s in sessions if s.get("agentConfigurationId") == args.agent
        ]

    sessions.sort(key=_activity, reverse=True)
    sessions = sessions[: args.limit]

    if args.json:
        print(json.dumps(sessions, indent=2))
        return 0

    print(f"{len(sessions)} session(s) (newest first)\n")
    print(f"{'session id':36}  {'status':9}  {'msgs':>4}  {'last activity':24}  title")
    print("-" * 110)
    for s in sessions:
        sid = s.get("id", "")
        status = s.get("status", "")
        msgs = s.get("messageCount", "")
        last = _activity(s)
        title = (s.get("title") or "")[:40]
        agent = s.get("agentConfigurationId")
        agent_tag = f"  [agent={agent[:8]}..]" if agent else ""
        print(f"{sid:36}  {status:9}  {str(msgs):>4}  {last:24}  {title}{agent_tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
