"""Minimal Quix AI chat client bound to a configured agent.

Sessions are created against `config.AGENT_CONFIGURATION_ID` so the
AC Telemetry Agent's system prompt + knowledge bases + MCP tools are in
scope from turn 1. The workspace id rides in the request `context` field; the
backend wraps it into the LLM-facing `<context>` block (so delegate_task can spawn
an environment agent) while the stored message stays clean. Otherwise no inline
instructions, sessions list, or channels dump.

    POST /ai/api/sessions
        body={"agentConfigurationId": <id>}              -> 200 {id, ...}
    POST /ai/api/sessions/{id}/messages
        body={"message": "...", "context": {}}           -> 200 SSE stream
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

import config

logger = logging.getLogger(__name__)


async def create_session(client: httpx.AsyncClient, token: str) -> str:
    """Open a Quix AI session bound to the QuixLake Querier agent.

    Authenticates as `token` (the logged-in user's Bearer) so the session is
    owned by that user. Returns the session UUID.
    """
    r = await client.post(
        f"{config.PORTAL}/ai/api/sessions",
        headers=config.portal_headers(token),
        json={"agentConfigurationId": config.AGENT_CONFIGURATION_ID},
    )
    r.raise_for_status()
    data = r.json()
    session_id = data.get("id") or data.get("sessionId")
    if not session_id:
        raise httpx.HTTPError("Quix Portal session response missing 'id'/'sessionId'")
    logger.info(
        "quix_ai: opened session %s (agent=%s)",
        session_id,
        config.AGENT_CONFIGURATION_ID,
    )
    return session_id


async def stream_message(
    client: httpx.AsyncClient, session_id: str, message: str, token: str
) -> AsyncIterator[dict]:
    """POST a user message, yield parsed SSE event dicts.

    Authenticates as `token` (the same user Bearer used to open the session).
    Filters out `data: [DONE]` sentinels and non-JSON keep-alive lines.
    Yields raw event dicts so the caller decides which to forward, buffer,
    or ignore (e.g. `text_delta` for streaming back, `usage` for logging).
    """
    url = f"{config.PORTAL}/ai/api/sessions/{session_id}/messages"
    body = {"message": message, "context": _workspace_context()}
    logger.debug("quix_ai: POST %s (%d chars message)", url, len(message))
    async with client.stream(
        "POST", url, headers=config.portal_headers(token, streaming=True), json=body
    ) as r:
        if r.status_code != 200:
            err_body = await r.aread()
            logger.warning(
                "quix_ai: upstream %d on message POST — body: %s",
                r.status_code,
                err_body[:1000].decode("utf-8", errors="replace"),
            )
            yield {"type": "error", "status": r.status_code}
            return
        # Named SSE frames: `event: <type>\ndata: <json>\n\n`. The event name is
        # authoritative (the JSON body may omit/differ on `type`), so we track
        # the latest `event:` line and stamp it onto the parsed dict — mirroring
        # the Quix AI reference consumer. `: heartbeat` comment lines are ignored.
        current_type = ""
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                current_type = line[7:].strip()
                continue
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]" or current_type == "done":
                logger.debug("quix_ai: stream [DONE]")
                return
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if current_type:
                evt["type"] = current_type
            logger.debug("quix_ai event: %s", _short(evt))
            yield evt
            current_type = ""  # one event per frame; don't leak type to a bare data: line


def _workspace_context() -> dict[str, str]:
    """Request `context` dict carrying the workspace id.

    The backend wraps this into the LLM-facing `<context>{...}</context>` block
    (UIContext.ToLlmBlock), so the agent can fill `delegate_task`'s `workspace_id`
    while the stored/displayed user message stays clean. Empty when WORKSPACE_ID unset.
    """
    return {"workspaceId": config.WORKSPACE_ID} if config.WORKSPACE_ID else {}


def _short(evt: dict, limit: int = 200) -> str:
    """Compact one Quix AI SSE event into a single-line log string.

    `text_delta` events appear many times per turn; keep them short so
    DEBUG logs stay scannable. Other event types log verbatim.
    """
    if evt.get("type") == "text_delta":
        text = evt.get("text", "")
        return f"text_delta: {text[:limit]!r}{'…' if len(text) > limit else ''}"
    return json.dumps(evt)[:500]
