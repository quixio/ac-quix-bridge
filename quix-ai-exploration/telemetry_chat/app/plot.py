"""Plot orchestration — POST /api/plot.

Flow:
  1. Validate request body.
  2. First turn (no session_id): create Quix AI chat session + build the
     first-turn message with instructions + channels + sessions.
     Subsequent turns: reuse session_id + send raw user message.
  3. Stream the agent's reply to a buffer, parse the final fenced JSON.
  4. If `clarify`: return it to the frontend as-is.
     If `plot`: fan out get_telemetry() per trace (concurrent, capped),
                return {session_id, type: 'plot', title, signal, traces,
                         track} where each trace has `{x, y, name, ...}`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, cast

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from .channels import raw_channels
from .plot_prompt import build_first_turn_message
from .quix_ai import collect_text, create_session
from .sessions_cache import get_sessions
from .telemetry import get_telemetry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_TRACES = 6
MAX_SIGNALS = 10
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# Agent's reply is a free-form message ending with ```json ... ```. The regex
# captures the LAST fenced JSON block so the agent can "think out loud" before
# committing to a final decision.
JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)


class PlotRequest(BaseModel):
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


@router.post("/plot")
async def plot(req: PlotRequest) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=120.0) as client:
        session_id = req.session_id
        if session_id is None:
            sessions = await get_sessions()
            session_id = await create_session(client)
            outbound = build_first_turn_message(
                user_message=req.message, sessions=sessions
            )
        else:
            outbound = req.message

        try:
            reply = await collect_text(client, session_id, outbound)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e

    parsed = _extract_json(reply)
    kind = parsed.get("type")

    if kind == "clarify":
        return {
            "session_id": session_id,
            "type": "clarify",
            "question": parsed.get("question", ""),
            "options": parsed.get("options", []),
        }

    if kind == "plot":
        return await _resolve_plot(session_id, parsed)

    raise HTTPException(
        status_code=502,
        detail=f"Agent returned unexpected type: {kind!r}",
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the last ```json … ``` block out of the agent's reply."""
    matches = JSON_FENCE_RE.findall(text)
    if not matches:
        raise HTTPException(
            status_code=502,
            detail="Agent response did not contain a ```json``` block.",
        )
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Agent JSON did not parse: {e}",
        ) from e
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=502,
            detail="Agent JSON was not an object.",
        )
    return parsed


def _extract_signals(parsed: dict[str, Any]) -> list[str]:
    """Accept `signals: [...]` (preferred) or legacy `signal: "..."` (single).
    Strip any `[unit]` tag the agent may have copied from the display form.
    """
    raw = parsed.get("signals")
    if raw is None and "signal" in parsed:
        raw = [parsed["signal"]]
    if not isinstance(raw, list) or not raw:
        raise HTTPException(status_code=502, detail="plot.signals missing or invalid")
    signals: list[str] = []
    for s in raw:
        if not isinstance(s, str) or not s:
            raise HTTPException(
                status_code=502, detail=f"plot.signals entry invalid: {s!r}"
            )
        clean = s.split("[", 1)[0].strip()
        if clean not in raw_channels():
            raise HTTPException(
                status_code=502,
                detail=f"plot.signals entry '{clean}' is not a known channel",
            )
        signals.append(clean)
    return signals


async def _resolve_plot(session_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    signals = _extract_signals(parsed)
    if len(signals) > MAX_SIGNALS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many signals ({len(signals)}); cap is {MAX_SIGNALS}.",
        )

    traces_in = parsed.get("traces")
    if not isinstance(traces_in, list) or not traces_in:
        raise HTTPException(status_code=502, detail="plot.traces missing or empty")
    if len(traces_in) > MAX_TRACES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many traces ({len(traces_in)}); cap is {MAX_TRACES}. "
            "Ask the agent to narrow the selection.",
        )

    # Validate every trace upfront so malformed entries surface as a 502
    # here, not as a silent drop inside asyncio.gather(return_exceptions=True).
    for i, raw in enumerate(traces_in):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=502, detail=f"trace[{i}] not an object")
        # `dict` is invariant in its key type; after narrowing, ty views `raw`
        # as `dict[Unknown, Unknown]` and won't auto-widen to `dict[str, Any]`.
        # Values are genuinely dynamic here (JSON from the LLM), so cast.
        trace = cast(dict[str, Any], raw)
        if not isinstance(trace.get("lap"), int):
            raise HTTPException(
                status_code=502,
                detail=f"trace[{i}].lap must be int, got {trace.get('lap')!r}",
            )

    tracks = {t["track"] for t in traces_in if isinstance(t, dict) and t.get("track")}
    if len(tracks) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"Traces span multiple tracks ({sorted(tracks)}); "
            "normalizedCarPosition overlay is meaningless across tracks.",
        )

    # Fan out signals × traces in one asyncio.gather — peak 10 × 6 = 60 concurrent
    # lake queries. httpx's lake pool is sized to 80 for this (see lake.py).
    jobs = [(signal, trace) for signal in signals for trace in traces_in]
    fetched = await asyncio.gather(
        *(_fetch_one(trace, signal) for signal, trace in jobs),
        return_exceptions=True,
    )

    # Bucket results back into one chart per signal.
    charts: list[dict[str, Any]] = []
    idx = 0
    for signal in signals:
        resolved: list[dict[str, Any]] = []
        for _ in traces_in:
            result = fetched[idx]
            idx += 1
            if isinstance(result, BaseException):
                logger.warning("signal=%s trace fetch failed: %s", signal, result)
                continue
            resolved.append(result)
        charts.append({"signal": signal, "traces": resolved})

    return {
        "session_id": session_id,
        "type": "plot",
        "title": parsed.get("title", ""),
        "track": next(iter(tracks), None),
        "charts": charts,
    }


async def _fetch_one(trace: dict[str, Any], signal: str) -> dict[str, Any]:
    # Shape pre-validated by _resolve_plot before the fan-out; `lap` is int.
    lap = trace["lap"]
    data = await get_telemetry(
        lap=lap,
        signals=[signal],
        environment=str(trace.get("environment", "")),
        test_rig=str(trace.get("test_rig", "")),
        experiment=str(trace.get("experiment", "")),
        driver=str(trace.get("driver", "")),
        track=str(trace.get("track", "")),
        carModel=str(trace.get("carModel", "")),
        session_id=str(trace.get("session_id", "")),
    )
    return {
        "session_id": trace.get("session_id"),
        "lap": lap,
        "driver": trace.get("driver"),
        "carModel": trace.get("carModel"),
        "track": trace.get("track"),
        "experiment": trace.get("experiment"),
        "x": data["data"].get("normalizedCarPosition", []),
        "y": data["data"].get(signal, []),
        "count": data["count"],
    }
