"""Plot orchestration — POST /api/plot streams progress as JSONL.

Response is newline-delimited JSON so the frontend can render phase-level
progress as it arrives. Event shapes:

    {"event": "status",   "message": "...", "done?": int, "total?": int, "session_id?": str}
    {"event": "clarify",  "session_id": str, "question": str, "options": list[str]}
    {"event": "plot",     "session_id": str, "title": str, "track": str|null, "charts": [...]}
    {"event": "error",    "session_id?": str, "detail": str, "status": int}

Flow:
  1. Validate the request body (Pydantic).
  2. First turn (no session_id): open a Quix AI session bound to the
     QuixLake Querier agent. Subsequent turns reuse the session_id.
     Either way, we forward only the raw user message — the agent's
     system prompt + KBs already supply instructions, channels, and the
     sessions inventory.
  3. Emit "Asking the agent" while it works, "Fetching telemetry N/total"
     per completed lake fetch (via asyncio.as_completed for live
     progress), and a final plot|clarify event.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .channels import raw_channels
from .quix_ai import create_session, stream_message
from .telemetry import get_telemetry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

MAX_TRACES = 6
MAX_SIGNALS = 10
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# Agent's reply is free-form and ends with ```json ... ```. The regex captures
# the LAST fenced JSON block so the agent can "think out loud" beforehand.
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
async def plot(req: PlotRequest) -> StreamingResponse:
    return StreamingResponse(
        _plot_events(req),
        media_type="application/x-ndjson",
        # Disable proxy/nginx buffering so the browser sees events in real
        # time instead of one big chunk at the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ───────────────────────── event helpers ─────────────────────────


def _event(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def _error_event(session_id: str | None, detail: str | Any, status: int = 502) -> bytes:
    logger.warning("plot error: %s (status=%d, session=%s)", detail, status, session_id)
    return _event(
        {
            "event": "error",
            "session_id": session_id,
            "detail": str(detail),
            "status": status,
        }
    )


# ───────────────────────── main stream ─────────────────────────


async def _plot_events(req: PlotRequest) -> AsyncIterator[bytes]:
    """Drive one /api/plot call end to end, yielding JSONL events."""
    t_start = time.monotonic()
    logger.info(
        "plot start: msg=%r session=%s",
        req.message[:80],
        req.session_id or "<new>",
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        session_id = req.session_id
        if session_id is None:
            session_id = await create_session(client)

        yield _event(
            {
                "event": "status",
                "message": "Asking the agent",
                "session_id": session_id,
            }
        )
        # Stream prose chunks straight through to the browser (`answer_delta`).
        # Stop streaming once the agent starts emitting a ```json``` block —
        # that's machine-readable and stays in the backend for plan parsing.
        # Hold back the trailing 6 chars of unstreamed accum so a fence opener
        # split across deltas (`"```"` then `"json..."`) never leaks visibly.
        accum = ""
        streamed = 0
        json_seen = False
        # len("```json") - 1 so we can always look one char ahead of the fence
        # opener before emitting.
        FENCE_LOOKAHEAD = 6
        async for evt in stream_message(client, session_id, req.message):
            t = evt.get("type")
            if t == "error":
                yield _error_event(session_id, f"upstream {evt.get('status')}")
                return
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
        # Stream any tail bytes we held back when no fence was ever seen
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
    t_after_agent = time.monotonic()

    if not reply.strip():
        yield _error_event(session_id, "Agent returned an empty reply")
        return

    if not JSON_FENCE_RE.search(reply):
        # Mode 2 / Mode 3 — agent answered in prose only. Already streamed.
        logger.info(
            "plot answer (no JSON) in %.1fs: %d chars",
            time.monotonic() - t_start,
            streamed,
        )
        return

    try:
        parsed = _extract_json(reply)
    except HTTPException as e:
        yield _error_event(session_id, e.detail, e.status_code)
        return

    kind = parsed.get("type")
    if kind == "clarify":
        logger.info(
            "plot clarify in %.1fs: %s",
            time.monotonic() - t_start,
            str(parsed.get("question", ""))[:100],
        )
        yield _event(
            {
                "event": "clarify",
                "session_id": session_id,
                "question": parsed.get("question", ""),
                "options": parsed.get("options", []),
            }
        )
        return
    if kind != "plot":
        yield _error_event(session_id, f"Agent returned unexpected type: {kind!r}")
        return

    try:
        plan = _plan(parsed)
    except HTTPException as e:
        yield _error_event(session_id, e.detail, e.status_code)
        return

    signals: list[str] = plan["signals"]
    traces_in: list[dict[str, Any]] = plan["traces"]
    total = len(traces_in)
    logger.info(
        "plot plan: signals=%s traces=%d track=%s (agent %.1fs)",
        signals,
        total,
        plan["track"],
        t_after_agent - t_start,
    )
    yield _event(
        {
            "event": "status",
            "message": "Fetching telemetry",
            "done": 0,
            "total": total,
            "session_id": session_id,
        }
    )

    # Fan out ONE lake query per (partition, lap) that selects every requested
    # signal at once — same pattern as telemetry-comparison's /api/telemetry.
    # Each lap's parquet partition is scanned a single time regardless of how
    # many signals the user asked for. Pre-allocate result slots so trace
    # order (and therefore colour order) survives as_completed.
    charts_rows: list[list[dict[str, Any] | None]] = [[None] * total for _ in signals]

    async def _one(
        ti: int, trace: dict[str, Any]
    ) -> tuple[int, dict[str, dict[str, Any]] | BaseException]:
        try:
            return (ti, await _fetch_trace(trace, signals))
        except BaseException as exc:  # noqa: BLE001 — we re-classify in the loop
            return (ti, exc)

    tasks = [asyncio.create_task(_one(ti, trace)) for ti, trace in enumerate(traces_in)]

    done = 0
    for future in asyncio.as_completed(tasks):
        ti, result = await future
        if isinstance(result, BaseException):
            logger.warning(
                "trace lap=%s fetch failed: %s",
                traces_in[ti].get("lap"),
                result,
            )
        else:
            for si, signal in enumerate(signals):
                charts_rows[si][ti] = result.get(signal)
        done += 1
        yield _event(
            {
                "event": "status",
                "message": "Fetching telemetry",
                "done": done,
                "total": total,
                "session_id": session_id,
            }
        )

    charts = [
        {"signal": signals[si], "traces": [t for t in row if t is not None]}
        for si, row in enumerate(charts_rows)
    ]
    t_end = time.monotonic()
    failures = total - (len(charts[0]["traces"]) if charts else 0)
    logger.info(
        "plot done in %.1fs (agent %.1fs + fan-out %.1fs): %d charts × %d traces%s",
        t_end - t_start,
        t_after_agent - t_start,
        t_end - t_after_agent,
        len(charts),
        total,
        f", {failures} fetch failure(s)" if failures else "",
    )

    yield _event(
        {
            "event": "plot",
            "session_id": session_id,
            "title": plan["title"],
            "track": plan["track"],
            "charts": charts,
        }
    )


# ───────────────────────── parsing + validation ─────────────────────────


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


def _plan(parsed: dict[str, Any]) -> dict[str, Any]:
    """Validate a 'plot' JSON and return the plan (signals/traces/title/track).

    Synchronous — runs all the cheap, deterministic checks before any lake
    fetches so failures surface immediately as a 4xx/502, not as a silent
    drop inside asyncio.gather.
    """
    signals = _extract_signals(parsed)
    if len(signals) > MAX_SIGNALS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many signals ({len(signals)}); cap is {MAX_SIGNALS}.",
        )

    raw_traces = parsed.get("traces")
    if not isinstance(raw_traces, list) or not raw_traces:
        raise HTTPException(status_code=502, detail="plot.traces missing or empty")
    if len(raw_traces) > MAX_TRACES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many traces ({len(raw_traces)}); cap is {MAX_TRACES}. "
            "Ask the agent to narrow the selection.",
        )

    traces: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_traces):
        if not isinstance(raw, dict):
            raise HTTPException(status_code=502, detail=f"trace[{i}] not an object")
        trace = cast(dict[str, Any], raw)
        if not isinstance(trace.get("lap"), int):
            raise HTTPException(
                status_code=502,
                detail=f"trace[{i}].lap must be int, got {trace.get('lap')!r}",
            )
        traces.append(trace)

    tracks = {t["track"] for t in traces if t.get("track")}
    if len(tracks) > 1:
        raise HTTPException(
            status_code=400,
            detail=f"Traces span multiple tracks ({sorted(tracks)}); "
            "normalizedCarPosition overlay is meaningless across tracks.",
        )

    return {
        "signals": signals,
        "traces": traces,
        "title": parsed.get("title", ""),
        "track": next(iter(tracks), None),
    }


async def _fetch_trace(
    trace: dict[str, Any], signals: list[str]
) -> dict[str, dict[str, Any]]:
    """Fetch every requested signal for one (partition, lap) in a SINGLE
    lake query. Returns a map `{signal: frontend-trace-dict}` so the caller
    can bucket each per-signal trace into its matching chart.

    Shape pre-validated by `_plan` before the fan-out; `lap` is int.
    """
    lap = trace["lap"]
    data = await get_telemetry(
        lap=lap,
        signals=signals,
        environment=str(trace.get("environment", "")),
        test_rig=str(trace.get("test_rig", "")),
        experiment=str(trace.get("experiment", "")),
        driver=str(trace.get("driver", "")),
        track=str(trace.get("track", "")),
        carModel=str(trace.get("carModel", "")),
        session_id=str(trace.get("session_id", "")),
    )
    x = data["data"].get("normalizedCarPosition", [])
    meta = {
        "session_id": trace.get("session_id"),
        "lap": lap,
        "driver": trace.get("driver"),
        "carModel": trace.get("carModel"),
        "track": trace.get("track"),
        "experiment": trace.get("experiment"),
        "count": data["count"],
    }
    return {
        signal: {**meta, "x": x, "y": data["data"].get(signal, [])}
        for signal in signals
    }
