"""Batch AI analysis runner — holds a Quix.AI SSE session for one analysis.

Exposed as `BatchAnalysisAI` so multiple consumers (Test Manager backend,
stream-processing service, Quixlab notebook) can share the same lifecycle.

Per spec §3 + §5:
  1. Open session via POST /ai/api/sessions
  2. Send seed message with workspaceId context
  3. Read events silently from the response stream
  4. Update analysis.status as we see tool_call_starts (fetching/analyzing/saving)
  5. Persist model + token counts + duration on usage event
  6. Hold connection for the full duration of the run; 15-min hard timeout via wait_for
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


HARD_TIMEOUT_SECONDS: float = 900  # 15 min (test-wide iterates per-session)
ORPHAN_THRESHOLD = timedelta(minutes=20)
NON_TERMINAL = {"pending", "running", "fetching", "analyzing", "saving"}


class BatchAnalysisAI:
    """Run + lifecycle-manage one Quix.AI post-race analysis.

    Construction takes the mongo handle and (optionally) the Quix.AI config.
    When portal_url / agent_id / workspace_id / quix_token are None, they are
    read from `Quix__Portal__Api` / `POST_RACE_AGENT_ID` /
    `Quix__Workspace__Id` / `PAT_TOKEN` env vars at `run()` time. The `Quix__*`
    vars are auto-injected in-cluster; `POST_RACE_AGENT_ID` / `PAT_TOKEN` are
    project variables (PAT_TOKEN is a user PAT — the service-account SDK token
    cannot use Quix AI).
    """

    def __init__(
        self,
        mongo: Database[dict[str, Any]],
        *,
        portal_url: str | None = None,
        agent_id: str | None = None,
        workspace_id: str | None = None,
        quix_token: str | None = None,
    ) -> None:
        self._mongo = mongo
        self._portal_url = portal_url
        self._agent_id = agent_id
        self._workspace_id = workspace_id
        self._quix_token = quix_token

    # --- public ----------------------------------------------------------- #

    async def run(
        self, *, analysis_id: str, test_id: str, session_id: str | None
    ) -> None:
        """Drive the analysis end-to-end. Wraps `_run_inner` with the hard
        timeout + failure handling — never raises; outcome lives in the
        analysis doc's status / error / error_kind fields."""
        try:
            await asyncio.wait_for(
                self._run_inner(
                    analysis_id=analysis_id,
                    test_id=test_id,
                    session_id=session_id,
                ),
                timeout=HARD_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            self._set_status(
                analysis_id,
                status="failed",
                error_kind="timeout",
                error=f"agent exceeded {HARD_TIMEOUT_SECONDS}s budget",
            )
            logger.warning("[runner] analysis %s failed — timeout", analysis_id)
        except Exception as exc:
            self._set_status(
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

    def cleanup_orphans(self) -> int:
        """Mark stuck non-terminal docs as failed with error_kind='orphan'.

        Intended to run at consumer startup so analyses interrupted by a
        previous process restart don't sit in `pending` forever.
        Returns the number of docs marked.
        """
        cutoff = datetime.now(timezone.utc) - ORPHAN_THRESHOLD
        result = self._mongo.analyses.update_many(
            {"status": {"$in": list(NON_TERMINAL)}, "updated_at": {"$lt": cutoff}},
            {
                "$set": {
                    "status": "failed",
                    "error_kind": "orphan",
                    "error": "Process restarted while analysis in progress",
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        if result.modified_count:
            logger.warning(
                "[runner] orphan sweep marked %d analyses failed",
                result.modified_count,
            )
        return result.modified_count

    # --- env-backed config ------------------------------------------------- #

    def _portal(self) -> str:
        url = self._portal_url or os.environ["Quix__Portal__Api"]
        return url.rstrip("/")

    def _resolved_agent_id(self) -> str:
        return self._agent_id or os.environ["POST_RACE_AGENT_ID"]

    def _resolved_workspace_id(self) -> str:
        return self._workspace_id or os.environ["Quix__Workspace__Id"]

    def _resolved_quix_token(self) -> str:
        return self._quix_token or os.environ["PAT_TOKEN"]

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._resolved_quix_token()}",
            "Content-Type": "application/json",
        }

    # --- helpers ----------------------------------------------------------- #

    def _seed_message(
        self, analysis_id: str, test_id: str, session_id: str | None
    ) -> dict[str, Any]:
        """Return the seed Quix.AI message. Branches on session_id (None = test-wide)."""
        if session_id is None:
            message = (
                "Analyze the entire test across ALL its recorded sessions.\n\n"
                f"analysis_id: {analysis_id}\n"
                f"test_id:     {test_id}\n"
                "scope:       test-wide\n\n"
                "Workflow:\n"
                "  1. Call list_sessions_for_test(test_id) to enumerate sessions.\n"
                "  2. Read the test's requirements via get_test(test_id).\n"
                "  3. Pull logbook with list_logbook(test_id, include_test_wide=True).\n"
                "  4. Query the lake per session (partition-filter on full tuple).\n"
                "  5. Compose cross-session insights; tag each KPI/anomaly with session_id.\n"
                "  6. Call save_analysis(analysis_id, payload={...}) exactly once.\n"
            )
        else:
            message = (
                "Analyze the racing session below.\n\n"
                f"analysis_id: {analysis_id}\n"
                f"test_id:     {test_id}\n"
                f"session_id:  {session_id}\n\n"
                "Workspace context: AC telemetry. Resolve the lake table per your "
                "instructions (do not assume a table name).\n\n"
                f'Call save_analysis(analysis_id="{analysis_id}", payload={{...}}) exactly once when done.'
            )
        return {
            "message": message,
            "context": {"workspaceId": self._resolved_workspace_id()},
        }

    @staticmethod
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

    @staticmethod
    async def _read_sse_events(
        response: httpx.Response,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed SSE event dicts from an httpx streamed response."""
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            raw = line[len("data:") :].strip()
            if raw == "[DONE]":
                return  # server signals end; stop iterating
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("[runner] skipping non-JSON SSE line: %r", raw)
                continue

    def _set_status(self, analysis_id: str, **fields: Any) -> None:
        """Persist fields with a fresh updated_at.

        `status` transitions are gated: if the current doc is already in a
        terminal state (complete/failed) — typically because the MCP
        `save_analysis` tool flipped it to complete out-of-band — we leave
        status alone and only persist the non-status fields (e.g. model,
        tokens). This preserves the MCP write path's authority over the
        terminal transition.
        """
        fields["updated_at"] = datetime.now(timezone.utc)
        if "status" in fields:
            doc = self._mongo.analyses.find_one(
                {"_id": analysis_id}, projection={"status": 1}
            )
            if doc and doc.get("status") in ("complete", "failed"):
                fields.pop("status")
                if len(fields) == 1:  # only updated_at left
                    return
        self._mongo.analyses.update_one({"_id": analysis_id}, {"$set": fields})

    async def _run_inner(
        self,
        *,
        analysis_id: str,
        test_id: str,
        session_id: str | None,
    ) -> None:
        portal = self._portal()
        agent_id = self._resolved_agent_id()

        started_wall = time.perf_counter()

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, read=None),
            headers=self._auth_headers(),
        ) as client:
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

            self._set_status(analysis_id, status="running", quix_session_id=qsess)
            logger.info("[runner] analysis %s started qsess=%s", analysis_id, qsess)

            # 2. Send seed + read SSE
            url = f"{portal}/ai/api/sessions/{qsess}/messages"
            async with client.stream(
                "POST",
                url,
                json=self._seed_message(analysis_id, test_id, session_id),
            ) as stream:
                stream.raise_for_status()
                async for evt in self._read_sse_events(stream):
                    etype = evt.get("type")
                    if etype == "tool_call_start":
                        new_status = self._classify_status_from_tool_name(
                            evt.get("toolName")
                        )
                        if new_status:
                            self._set_status(analysis_id, status=new_status)
                    elif etype == "usage":
                        self._set_status(
                            analysis_id,
                            model=evt.get("model"),
                            tokens_in=evt.get("inputTokens"),
                            tokens_out=evt.get("outputTokens"),
                            tokens_cache_create=evt.get("cacheCreationInputTokens"),
                            tokens_cache_read=evt.get("cacheReadInputTokens"),
                        )
                    elif etype == "error":
                        # Quix.AI ChatStreamEvent: agent / framework failure.
                        msg = evt.get("message") or "agent stream error"
                        self._set_status(
                            analysis_id,
                            status="failed",
                            error_kind="agent",
                            error=f"{evt.get('code') or 'error'}: {msg}",
                        )
                        logger.warning(
                            "[runner] analysis %s — SSE error: %s", analysis_id, msg
                        )
                        return  # bail; outer wait_for context will tidy up
                    elif etype == "agent_disabled":
                        # Admin toggled the agent off mid-run. Treat as terminal.
                        self._set_status(
                            analysis_id,
                            status="failed",
                            error_kind="agent",
                            error="agent_disabled mid-run",
                        )
                        logger.warning(
                            "[runner] analysis %s — agent_disabled", analysis_id
                        )
                        return

        # 3. Stream ended. If MCP save_analysis hasn't flipped status to
        #    complete, agent didn't follow protocol — mark failed.
        doc = self._mongo.analyses.find_one({"_id": analysis_id})
        duration_ms = int((time.perf_counter() - started_wall) * 1000)
        if doc and doc["status"] != "complete":
            self._set_status(
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
            self._set_status(analysis_id, duration_ms=duration_ms)
            logger.info(
                "[runner] analysis %s completed in %dms", analysis_id, duration_ms
            )
