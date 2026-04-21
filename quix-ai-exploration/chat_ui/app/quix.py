"""Thin async client around the Quix Portal AI endpoints we actually use."""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .config import PORTAL, portal_context, portal_headers


async def create_workspace_session(client: httpx.AsyncClient) -> str:
    r = await client.post(
        f"{PORTAL}/ai/api/sessions",
        headers=portal_headers(),
        json={"context": portal_context()},
    )
    r.raise_for_status()
    data = r.json()
    return data.get("id") or data["sessionId"]


def _error_frame(status: int) -> bytes:
    """Upstream error → single SSE frame with status only (no body forwarding)."""
    return f"event: error\ndata: {json.dumps({'status': status})}\n\n".encode()


async def stream_workspace_message(
    client: httpx.AsyncClient, session_id: str, message: str
) -> AsyncIterator[bytes]:
    """Forward the QuixAI SSE stream verbatim. Caller injects any synthetic
    pre/post events (e.g. a session frame for the browser)."""
    url = f"{PORTAL}/ai/api/sessions/{session_id}/messages"
    body = {"message": message, "context": portal_context()}
    async with client.stream(
        "POST", url, headers=portal_headers(streaming=True), json=body
    ) as r:
        if r.status_code != 200:
            await r.aread()
            yield _error_frame(r.status_code)
            return
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        except httpx.HTTPError:
            yield _error_frame(502)
