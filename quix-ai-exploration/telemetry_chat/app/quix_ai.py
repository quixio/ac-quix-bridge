"""Minimal Quix AI chat client — no workspace context.

Validated against `portal-api.dev.quix.io` by
`quix-ai-exploration/probes/probe_chat_no_workspace.py`:

    POST /ai/api/sessions        body={}                 → 200 {id, ...}
    POST /ai/api/sessions/{id}/messages
       body={"message": "...", "context": {}}            → 200 SSE stream

Request shapes above are undocumented in the public Portal OpenAPI spec; if
they change, update this module and re-run the probe.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

from . import config

logger = logging.getLogger(__name__)


async def create_session(client: httpx.AsyncClient) -> str:
    """Open a new workspace-less Quix AI chat session. Returns the session UUID."""
    r = await client.post(
        f"{config.PORTAL}/ai/api/sessions",
        headers=config.portal_headers(),
        json={},
    )
    r.raise_for_status()
    data = r.json()
    session_id = data.get("id") or data["sessionId"]
    logger.info("quix_ai: opened session %s", session_id)
    return session_id


async def stream_message(
    client: httpx.AsyncClient, session_id: str, message: str
) -> AsyncIterator[dict]:
    """POST a user message, yield parsed SSE event dicts (type + payload).

    Filters out `data: [DONE]` sentinels and non-JSON keep-alive lines. Yields
    raw event dicts so the caller can decide which to forward/buffer/ignore
    (e.g. `text_delta` for streaming back, `usage` for logging).
    """
    url = f"{config.PORTAL}/ai/api/sessions/{session_id}/messages"
    body = {"message": message, "context": {}}
    logger.debug("quix_ai: POST %s (%d chars message)", url, len(message))
    async with client.stream(
        "POST", url, headers=config.portal_headers(streaming=True), json=body
    ) as r:
        if r.status_code != 200:
            body = await r.aread()
            logger.warning(
                "quix_ai: upstream %d on message POST — body: %s",
                r.status_code,
                body[:1000].decode("utf-8", errors="replace"),
            )
            yield {"type": "error", "status": r.status_code}
            return
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                logger.debug("quix_ai: stream [DONE]")
                return
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            # DEBUG: full event dict, truncated. At INFO this stays silent —
            # one plot request emits dozens of text_delta events.
            logger.debug("quix_ai event: %s", _short(evt))
            yield evt


def _short(evt: dict, limit: int = 200) -> str:
    """Compact a Quix AI SSE event dict into a single-line log string.

    `text_delta` events appear many times per turn; keep them short so the
    debug log stays scannable. Other event types log verbatim so we can
    see tool-use, usage, and status frames as they arrive.
    """
    if evt.get("type") == "text_delta":
        text = evt.get("text", "")
        return f"text_delta: {text[:limit]!r}{'…' if len(text) > limit else ''}"
    return json.dumps(evt)[:500]


async def collect_text(client: httpx.AsyncClient, session_id: str, message: str) -> str:
    """Send a message and collect the full assistant text reply.

    Buffers all `text_delta` events into one string. The chat orchestrator
    uses this when it only cares about the final structured JSON output, not
    about streaming intermediate tokens to the browser.
    """
    parts: list[str] = []
    async for evt in stream_message(client, session_id, message):
        if evt.get("type") == "text_delta":
            parts.append(evt.get("text", ""))
        elif evt.get("type") == "error":
            raise RuntimeError(f"quix_ai upstream error: {evt.get('status')}")
    return "".join(parts)
