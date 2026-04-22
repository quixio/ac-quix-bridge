"""Per-lap telemetry fetcher. Adapted from telemetry-comparison/main.py's
`/api/telemetry` handler, minus the FastAPI wrapping — the chat orchestrator
calls this directly for each LLM-proposed trace."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException

from . import config
from .channels import raw_channels
from .lake import lake_query, sanitize_df
from .partitions import build_partition_filter

logger = logging.getLogger(__name__)

# Safety cap — a misconfigured lap should never return this much. At 60Hz a
# full 30-minute session is ~108k rows; anything > this is a bug or an abuse.
ROW_LIMIT = 50_000


async def get_telemetry(
    *,
    lap: int,
    signals: list[str],
    environment: str = "",
    test_rig: str = "",
    experiment: str = "",
    driver: str = "",
    track: str = "",
    carModel: str = "",  # noqa: N803 — partition column name matches lake schema
    session_id: str = "",
) -> dict[str, Any]:
    """Fetch one lap, sort by track position, trim pit approach on lap 1.

    Returns {session_id, lap, signals, count, data} — same shape as
    telemetry-comparison's `/api/telemetry` so the frontend trace-builder
    logic is portable.
    """
    # Defense in depth: even though callers (plot.py::_extract_signals)
    # already check this, validate here too so get_telemetry is safe to
    # call directly from tests or future routes. Allow-list via the known
    # channel set; isidentifier() catches truly malformed inputs.
    known = raw_channels()
    for s in signals:
        if not s.isidentifier():
            raise HTTPException(status_code=400, detail=f"Invalid signal name: {s}")
        if s not in known:
            raise HTTPException(status_code=400, detail=f"Unknown signal: {s}")

    columns = ", ".join(signals)
    try:
        where = build_partition_filter(
            environment=environment,
            test_rig=test_rig,
            experiment=experiment,
            driver=driver,
            track=track,
            carModel=carModel,
            session_id=session_id,
            lap=lap,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        sql = f"""
            SELECT
                normalizedCarPosition,
                timestamp_ms,
                {columns}
            FROM {config.TABLE_NAME}
            {where}
            LIMIT {ROW_LIMIT}
        """
        df = await lake_query(sql)
        df = df.sort_values("normalizedCarPosition").reset_index(drop=True)
        df = sanitize_df(df)

        # First-lap pit-exit trim (copied verbatim from telemetry-comparison):
        # either detect the race-start wrap (norm pos jumps from ~1.0 to ~0.0)
        # or drop out-laps that never cross 0.
        if lap == 1 and not df.empty:
            by_time = df.sort_values("timestamp_ms")
            ncp = by_time["normalizedCarPosition"].values
            trimmed = False
            for i in range(1, len(ncp)):
                if ncp[i - 1] > 0.9 and ncp[i] < 0.1:
                    df = by_time.iloc[i:].sort_values("normalizedCarPosition")
                    trimmed = True
                    break
            if not trimmed:
                min_ncp = df["normalizedCarPosition"].min()
                if min_ncp is not None and min_ncp > 0.1:
                    df = df.iloc[0:0]

        return {
            "session_id": session_id,
            "lap": lap,
            "signals": signals,
            "count": len(df),
            "data": df.to_dict(orient="list"),
        }
    except HTTPException:
        raise
    except httpx.TimeoutException as e:
        logger.warning("QuixLake timed out: %s", e)
        raise HTTPException(status_code=504, detail=f"Data lake timed out: {e}") from e
    except Exception as e:
        logger.exception("Failed to get telemetry")
        raise HTTPException(status_code=500, detail=str(e)) from e
