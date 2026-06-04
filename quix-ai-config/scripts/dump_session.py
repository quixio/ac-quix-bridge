# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "python-dotenv"]
# ///
"""Dump a Quix.AI chat session: detail + every message, tool calls and errors.

Usage:
    uv run scripts/dump_session.py <session-id>
    uv run scripts/dump_session.py --last                 # newest Post-Race session
    uv run scripts/dump_session.py --last --agent <id>    # newest on that agent
    uv run scripts/dump_session.py <session-id> --json     # raw message JSON
    uv run scripts/dump_session.py <session-id> --full     # no truncation
    uv run scripts/dump_session.py <session-id> --env-agents  # expand sub-agents

Highlights tool_use / tool_result blocks so you can see which MCP tools the
agent called, what arguments it sent, and which results came back as errors —
the common "agent kept going after an MCP tool failed" debug case.

When the agent uses `delegate_task`, the sub-agent's tool calls do NOT appear
in the parent session's messages — they live in environment-agent `activities`
(GET /ai/api/sessions/{id}/environment-agents). A summary always prints;
--env-agents expands the full sub-agent activity narrative.

Messages page through GET /ai/api/sessions/{id}/messages (max 50 per page,
paginated by the `before` sequence number); this fetches all of them.
"""

from __future__ import annotations

import argparse
import json
import os

import httpx

from _common import portal, token

POST_RACE_AGENT_ID = "350c788d-d25f-4aea-a78c-61ebab32b059"
TRUNC = 600


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Accept": "application/json",
        "x-version": "2.0",
    }


def _activity(s: dict) -> str:
    return s.get("lastActivityAt") or s.get("updatedAt") or s.get("createdAt") or ""


def _resolve_last(client: httpx.Client, agent_id: str | None) -> str:
    r = client.get("/ai/api/sessions")
    r.raise_for_status()
    sessions = r.json()
    if agent_id:
        sessions = [s for s in sessions if s.get("agentConfigurationId") == agent_id]
    if not sessions:
        raise SystemExit("no matching sessions found")
    sessions.sort(key=_activity, reverse=True)
    return sessions[0]["id"]


def _fetch_all_messages(client: httpx.Client, sid: str) -> list[dict]:
    """Page backwards through the message history and return oldest-first."""
    collected: dict[int, dict] = {}
    before: int | None = None
    while True:
        params: dict[str, int] = {"limit": 50}
        if before is not None:
            params["before"] = before
        r = client.get(f"/ai/api/sessions/{sid}/messages", params=params)
        r.raise_for_status()
        body = r.json()
        page = body.get("messages", []) if isinstance(body, dict) else body
        if not page:
            break
        for m in page:
            seq = m.get("sequenceNumber", 0)
            collected[seq] = m
        has_more = isinstance(body, dict) and body.get("hasMore")
        if not has_more:
            break
        before = min(m.get("sequenceNumber", 0) for m in page)
    return [collected[k] for k in sorted(collected)]


def _clip(text: str, full: bool) -> str:
    if full or len(text) <= TRUNC:
        return text
    return text[:TRUNC] + f"… [+{len(text) - TRUNC} chars]"


def _render_blocks(blocks: list[dict], full: bool, stats: dict) -> None:
    for b in blocks:
        btype = b.get("type")
        if btype == "text":
            txt = (b.get("text") or "").strip()
            if txt:
                print(f"    text: {_clip(txt, full)}")
        elif btype == "tool_use":
            stats["tool_calls"] += 1
            name = b.get("toolName") or b.get("displayName") or "?"
            args = b.get("arguments") or ""
            print(f"    → tool_use {name}  id={b.get('toolCallId', '')[:12]}")
            if args:
                print(f"        args: {_clip(str(args), full)}")
        elif btype == "tool_result":
            is_err = bool(b.get("isError"))
            tag = "ERROR" if is_err else "ok"
            if is_err:
                stats["tool_errors"] += 1
            result = b.get("result") or ""
            errtype = b.get("errorType")
            etag = f" errorType={errtype}" if errtype else ""
            print(f"    ← tool_result [{tag}]{etag}  id={b.get('toolCallId', '')[:12]}")
            # Always show full error bodies; truncate successful ones.
            print(f"        result: {_clip(str(result), full or is_err)}")
        else:
            print(f"    [{btype}] {_clip(json.dumps(b), full)}")


def _render_env_agents(agents: list[dict], full: bool, expand: bool) -> None:
    """Render delegate_task sub-agents. Their tool calls live in `activities`,
    NOT in the parent session's messages."""
    if not agents:
        return
    print("=== environment agents (delegate_task sub-agents) ===")
    for a in agents:
        acts = a.get("activities", [])
        kinds: dict[str, int] = {}
        for act in acts:
            kinds[act.get("kind", "?")] = kinds.get(act.get("kind", "?"), 0) + 1
        print(f"\nagent {a.get('id', '')[:12]}  status={a.get('status')}  "
              f"summary={a.get('summary')!r}")
        print(f"  workspace: {a.get('workspaceName')} ({a.get('workspaceId')})")
        print(f"  task: {_clip((a.get('task') or '').strip(), full)}")
        print(f"  activities: {len(acts)}  {kinds}")
        if not expand:
            continue
        for act in acts:
            kind = act.get("kind")
            data = act.get("data") or {}
            if kind == "tool_start":
                name = data.get("toolName") or data.get("name") or "?"
                payload = data.get("arguments") or data.get("command") or ""
                print(f"    ▶ {name}: {_clip(str(payload), full)}")
            elif kind == "tool_result":
                res = data.get("summary") or data.get("result") or data.get("output")
                tag = " [ERR]" if data.get("isError") else ""
                print(f"       ↳{tag} {_clip(str(res), full or bool(data.get('isError')))}")
            elif kind == "command":
                print(f"    $ {_clip(str(data.get('command') or data), full)}")
            elif kind == "file_edit":
                print(f"    ✎ {data.get('path')} ({data.get('linesChanged')} lines)")
            elif kind == "text":
                txt = data.get("text") if isinstance(data, dict) else data
                print(f"    💬 {_clip(str(txt), full)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id", nargs="?", help="session id to dump")
    parser.add_argument("--last", action="store_true", help="use newest session")
    parser.add_argument(
        "--agent",
        nargs="?",
        const=os.environ.get("QUIX_AI_POST_RACE_AGENT_ID", POST_RACE_AGENT_ID),
        default=os.environ.get("QUIX_AI_POST_RACE_AGENT_ID", POST_RACE_AGENT_ID),
        help="agent filter for --last (default: Post-Race Analyzer)",
    )
    parser.add_argument("--json", action="store_true", help="dump raw message JSON")
    parser.add_argument("--full", action="store_true", help="no truncation")
    parser.add_argument(
        "--env-agents",
        action="store_true",
        help="expand delegate_task sub-agent (environment agent) activity",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=portal(), headers=_headers(), timeout=60.0) as client:
        if args.last:
            sid = _resolve_last(client, args.agent)
        elif args.session_id:
            sid = args.session_id
        else:
            raise SystemExit("pass a session id or --last")

        detail = client.get(f"/ai/api/sessions/{sid}")
        detail.raise_for_status()
        d = detail.json()

        messages = _fetch_all_messages(client, sid)

        env_agents = client.get(f"/ai/api/sessions/{sid}/environment-agents")
        env_agents = env_agents.json() if env_agents.status_code == 200 else []

    if args.json:
        print(json.dumps({"messages": messages, "environmentAgents": env_agents}, indent=2))
        return 0

    usage = d.get("usage") or {}
    print(f"=== session {sid} ===")
    print(f"title:        {d.get('title')!r}")
    print(f"status:       {d.get('status')}")
    print(f"created:      {d.get('createdAt')}")
    print(f"lastActivity: {d.get('lastActivityAt')}")
    print(f"messageCount: {d.get('messageCount')}  (fetched {len(messages)})")
    print(f"compactions:  {d.get('compactionCount')}")
    print(
        "usage:        in={} out={} cache_w={} cache_r={}".format(
            usage.get("inputTokens", 0),
            usage.get("outputTokens", 0),
            usage.get("cacheCreationInputTokens", 0),
            usage.get("cacheReadInputTokens", 0),
        )
    )
    print()

    stats = {"tool_calls": 0, "tool_errors": 0}
    for m in messages:
        role = m.get("role", "?")
        seq = m.get("sequenceNumber", "?")
        created = m.get("createdAt", "")
        print(f"[{seq}] {role}  {created}")
        blocks = m.get("contentBlocks")
        if blocks:
            _render_blocks(blocks, args.full, stats)
        else:
            content = m.get("content")
            if content:
                print(f"    {_clip(str(content), args.full)}")
        print()

    _render_env_agents(env_agents, args.full, args.env_agents)

    print("\n=== summary ===")
    print(f"messages:        {len(messages)}")
    print(f"tool calls:      {stats['tool_calls']}")
    print(f"tool errors:     {stats['tool_errors']}")
    if env_agents:
        total_acts = sum(len(a.get("activities", [])) for a in env_agents)
        print(f"env agents:      {len(env_agents)}  ({total_acts} activities)")
        if not args.env_agents:
            print("                 (re-run with --env-agents to expand)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
