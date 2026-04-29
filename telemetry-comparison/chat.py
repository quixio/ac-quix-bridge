"""POST /api/chat — JSONL stream forwarding Quix AI agent output.

The Quix AI agent (QuixLake Querier) returns three flavours of reply:

    Mode 1 (Viz plan): prose + ```json {type: "plot" | "clarify"} ```
    Mode 2 (Analysis): prose only, may include MCP tool_call_start frames
    Mode 3 (Defer)   : short prose refusal, no tool calls, no JSON

We forward all of it to the browser as ndjson with these event shapes:

    {event: "status",       message: str, session_id?: str}
    {event: "answer_delta", session_id: str, text: str}
    {event: "answer_break", session_id: str}
    {event: "clarify",      session_id: str, question: str, options: list[str]}
    {event: "plot",         session_id: str, plan: dict}
    {event: "error",        session_id?: str, detail: str, status: int}

No backend lake fan-out — Explorer's existing /api/telemetry path renders
charts. We pass the raw plot plan through; the frontend's applyPlotPlan
drives the manual UI surfaces.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from plans import AgentPlan, ClarifyPlan, PlotPlan
from quix_ai import create_session, stream_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
FENCE_LOOKAHEAD = 6  # len("```json") - 1, kept back so a split fence never leaks


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not SESSION_ID_RE.fullmatch(v):
            raise ValueError("session_id must be 8-64 chars of [A-Za-z0-9_-]")
        return v


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _chat_events(req),
        media_type="application/x-ndjson",
        # Disable proxy/nginx buffering so the browser sees events live.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_PLAN_ADAPTER: TypeAdapter[AgentPlan] = TypeAdapter(AgentPlan)


def _event(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def _error_event(session_id: str | None, detail: str, status: int = 502) -> bytes:
    logger.warning("chat error: %s (status=%d, session=%s)", detail, status, session_id)
    return _event(
        {
            "event": "error",
            "session_id": session_id,
            "detail": detail,
            "status": status,
        }
    )


async def _chat_events(req: ChatRequest) -> AsyncIterator[bytes]:
    t_start = time.monotonic()
    logger.info(
        "chat start: msg=%r session=%s",
        req.message[:80],
        req.session_id or "<new>",
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        session_id = req.session_id
        if session_id is None:
            try:
                session_id = await create_session(client)
            except httpx.HTTPError as e:
                yield _error_event(None, f"Could not open Quix AI session: {e}")
                return

        yield _event(
            {
                "event": "status",
                "message": "Thinking…",
                "session_id": session_id,
            }
        )

        # Hold back the trailing FENCE_LOOKAHEAD chars of unstreamed accum so
        # a fence opener split across deltas (`"```"` then `"json..."`) never
        # leaks visibly to the browser.
        accum = ""
        streamed = 0
        json_seen = False

        async for evt in stream_message(client, session_id, req.message):
            t = evt.get("type")
            if t == "error":
                yield _error_event(session_id, f"upstream {evt.get('status')}")
                return
            if t == "tool_call_start" and not json_seen:
                # Flush any held tail of pre-tool prose, then break the bubble
                # so post-tool prose lands in a fresh assistant message.
                if streamed < len(accum):
                    yield _event(
                        {
                            "event": "answer_delta",
                            "session_id": session_id,
                            "text": accum[streamed:],
                        }
                    )
                    streamed = len(accum)
                yield _event({"event": "answer_break", "session_id": session_id})
                continue
            if t != "text_delta":
                continue
            accum += evt.get("text", "")
            if json_seen:
                continue
            fence_idx = accum.find("```json")
            if fence_idx >= 0:
                end = fence_idx
                json_seen = True
            else:
                end = max(streamed, len(accum) - FENCE_LOOKAHEAD)
            chunk = accum[streamed:end]
            if chunk:
                yield _event(
                    {
                        "event": "answer_delta",
                        "session_id": session_id,
                        "text": chunk,
                    }
                )
                streamed = end

        # Stream any tail bytes we held back when no fence ever appeared
        # (Mode 2 / Mode 3 prose answers).
        if not json_seen and streamed < len(accum):
            yield _event(
                {
                    "event": "answer_delta",
                    "session_id": session_id,
                    "text": accum[streamed:],
                }
            )

    reply = accum
    if not reply.strip():
        yield _error_event(session_id, "Agent returned an empty reply")
        return

    # No fence -> Mode 2 / Mode 3 prose answer; everything already streamed.
    if not JSON_FENCE_RE.search(reply):
        logger.info("chat answer (no JSON) in %.1fs", time.monotonic() - t_start)
        return

    try:
        parsed = _extract_json(reply)
    except HTTPException as e:
        yield _error_event(session_id, str(e.detail), e.status_code)
        return

    try:
        plan = _PLAN_ADAPTER.validate_python(parsed)
    except ValidationError as e:
        yield _error_event(session_id, f"Agent JSON shape invalid: {e}", 502)
        return

    if isinstance(plan, ClarifyPlan):
        logger.info("chat clarify in %.1fs: %s", time.monotonic() - t_start, plan.question[:100])
        yield _event(
            {
                "event": "clarify",
                "session_id": session_id,
                "question": plan.question,
                "options": plan.options,
            }
        )
        return

    assert isinstance(plan, PlotPlan)
    logger.info(
        "chat plot in %.1fs: %d signals × %d traces",
        time.monotonic() - t_start,
        len(plan.signals),
        len(plan.traces),
    )
    yield _event(
        {
            "event": "plot",
            "session_id": session_id,
            "plan": plan.model_dump(),
        }
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the LAST ```json … ``` block out of the agent's reply.

    The agent is allowed to "think out loud" before committing — we always
    take the final fence so accidental earlier code blocks don't poison
    the parse.
    """
    matches = JSON_FENCE_RE.findall(text)
    if not matches:
        raise HTTPException(
            status_code=502,
            detail="Agent response did not contain a ```json``` block.",
        )
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=502, detail=f"Agent JSON did not parse: {e}") from e
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Agent JSON was not an object.")
    return parsed
