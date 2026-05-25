"""Asyncio task that holds a Quix.AI SSE session for one analysis run.

Per spec §3 + §5:
  1. Open session via POST /ai/api/sessions
  2. Send seed message with workspaceId context
  3. Read events silently from the response stream
  4. Update analysis.status as we see tool_call_starts (fetching/analyzing/saving)
  5. Persist model + token counts + duration on usage event
  6. Hold connection for the full duration of the run; 5-min hard timeout via wait_for
  7. On any unexpected exit, mark failed with appropriate error_kind
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pymongo.database import Database

logger = logging.getLogger(__name__)


HARD_TIMEOUT_SECONDS: float = 300  # 5 minutes
ORPHAN_THRESHOLD = timedelta(minutes=10)
NON_TERMINAL = {"pending", "running", "fetching", "analyzing", "saving"}


def _portal() -> str:
    return os.environ["Quix__Portal__Api"].rstrip("/")


def _seed_message(analysis_id: str, test_id: str, session_id: str) -> dict[str, Any]:
    return {
        "message": (
            "Analyze the racing session below.\n\n"
            f"analysis_id: {analysis_id}\n"
            f"test_id:     {test_id}\n"
            f"session_id:  {session_id}\n\n"
            "Workspace context: AC telemetry, lake table = ac_telemetry.\n\n"
            f'Call save_analysis(analysis_id="{analysis_id}", payload={{...}}) exactly once when done.'
        ),
        "context": {"workspaceId": os.environ["Quix__Workspace__Id"]},
    }


def _classify_status_from_tool_name(tool_name: str | None) -> str | None:
    if not tool_name:
        return None
    if tool_name.startswith("mcp__test-manager__save_analysis"):
        return "saving"
    if tool_name.startswith("mcp__quixlake__") or tool_name == "delegate_task":
        return "analyzing"
    if tool_name.startswith("mcp__test-manager__"):
        return "fetching"
    return None


async def _read_sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    """Yield parsed SSE event dicts from an `httpx.Response` opened via client.stream()."""
    async for line in response.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        raw = line[len("data:") :].strip()
        if raw == "[DONE]":
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("[runner] skipping non-JSON SSE line: %r", raw)
            continue


def _set_status(
    mongo: Database[dict[str, Any]], analysis_id: str, **fields: Any
) -> None:
    """Persist fields with a fresh updated_at.

    `status` transitions are gated: if the current doc is already in a terminal
    state (complete/failed) — typically because the MCP `save_analysis` tool
    flipped it to complete out-of-band — we leave status alone and only persist
    the non-status fields (e.g. model, tokens). This preserves the MCP write
    path's authority over the terminal transition.
    """
    fields["updated_at"] = datetime.now(timezone.utc)
    if "status" in fields:
        # MCP save_analysis owns the complete transition; once a doc is
        # complete OR failed, no further status writes from the runner — even
        # `failed` — should overwrite. Drop the status field but keep any
        # non-status fields (e.g. duration_ms on the success tail).
        doc = mongo.analyses.find_one({"_id": analysis_id}, projection={"status": 1})
        if doc and doc.get("status") in ("complete", "failed"):
            fields.pop("status")
            if len(fields) == 1:  # only updated_at left
                return
    mongo.analyses.update_one({"_id": analysis_id}, {"$set": fields})


async def _run_inner(
    mongo: Database[dict[str, Any]],
    *,
    analysis_id: str,
    test_id: str,
    session_id: str,
) -> None:
    portal = _portal()
    agent_id = os.environ["QUIX_AI_POST_RACE_AGENT_ID"]

    started_wall = time.perf_counter()

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None)) as client:
        # 1. Open session
        resp = await client.post(
            f"{portal}/ai/api/sessions",
            json={"agentConfigurationId": agent_id},
        )
        resp.raise_for_status()
        body = resp.json()
        qsess = body.get("id") or body.get("sessionId")
        if not qsess:
            raise RuntimeError("Quix.AI session create response missing id")

        _set_status(mongo, analysis_id, status="running", quix_session_id=qsess)
        logger.info("[runner] analysis %s started qsess=%s", analysis_id, qsess)

        # 2. Send seed + read SSE
        url = f"{portal}/ai/api/sessions/{qsess}/messages"
        async with client.stream(
            "POST", url, json=_seed_message(analysis_id, test_id, session_id)
        ) as stream:
            stream.raise_for_status()
            async for evt in _read_sse_events(stream):
                etype = evt.get("type")
                if etype == "tool_call_start":
                    new_status = _classify_status_from_tool_name(evt.get("toolName"))
                    if new_status:
                        _set_status(mongo, analysis_id, status=new_status)
                elif etype == "usage":
                    _set_status(
                        mongo,
                        analysis_id,
                        model=evt.get("model"),
                        tokens_in=evt.get("inputTokens"),
                        tokens_out=evt.get("outputTokens"),
                        tokens_cache_create=evt.get("cacheCreationInputTokens"),
                        tokens_cache_read=evt.get("cacheReadInputTokens"),
                    )

    # 3. Stream ended. If MCP save_analysis hasn't already flipped status to complete,
    #    something went wrong — mark failed.
    doc = mongo.analyses.find_one({"_id": analysis_id})
    duration_ms = int((time.perf_counter() - started_wall) * 1000)
    if doc and doc["status"] != "complete":
        _set_status(
            mongo,
            analysis_id,
            status="failed",
            error_kind="agent",
            error="agent did not call save_analysis before stream end",
            duration_ms=duration_ms,
        )
        logger.warning(
            "[runner] analysis %s failed — agent no-save (duration=%dms)",
            analysis_id,
            duration_ms,
        )
    else:
        _set_status(mongo, analysis_id, duration_ms=duration_ms)
        logger.info("[runner] analysis %s completed in %dms", analysis_id, duration_ms)


async def run_analysis(
    mongo: Database[dict[str, Any]],
    *,
    analysis_id: str,
    test_id: str,
    session_id: str,
) -> None:
    """Public entry point. Wraps _run_inner with the 5-min hard timeout + failure handling."""
    try:
        await asyncio.wait_for(
            _run_inner(
                mongo,
                analysis_id=analysis_id,
                test_id=test_id,
                session_id=session_id,
            ),
            timeout=HARD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        _set_status(
            mongo,
            analysis_id,
            status="failed",
            error_kind="timeout",
            error=f"agent exceeded {HARD_TIMEOUT_SECONDS}s budget",
        )
        logger.warning("[runner] analysis %s failed — timeout", analysis_id)
    except Exception as exc:
        _set_status(
            mongo,
            analysis_id,
            status="failed",
            error_kind="agent",
            error=f"{type(exc).__name__}: {exc}",
        )
        logger.error(
            "[runner] analysis %s failed — %s: %s",
            analysis_id,
            type(exc).__name__,
            exc,
        )


def cleanup_orphans(mongo: Database[dict[str, Any]]) -> int:
    """On backend startup, mark stuck non-terminal docs as failed with error_kind='orphan'.

    Returns the number of docs marked.
    """
    cutoff = datetime.now(timezone.utc) - ORPHAN_THRESHOLD
    result = mongo.analyses.update_many(
        {"status": {"$in": list(NON_TERMINAL)}, "updated_at": {"$lt": cutoff}},
        {
            "$set": {
                "status": "failed",
                "error_kind": "orphan",
                "error": "Backend restarted while analysis in progress",
                "updated_at": datetime.now(timezone.utc),
            }
        },
    )
    if result.modified_count:
        logger.warning(
            "[runner] orphan sweep marked %d analyses failed", result.modified_count
        )
    return result.modified_count
