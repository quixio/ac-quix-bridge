"""QuixLake /query client. Copied + trimmed from telemetry-comparison/main.py."""

from __future__ import annotations

import io

import httpx
import pandas as pd
from fastapi import HTTPException

from . import config

_lake_http = httpx.AsyncClient(
    timeout=60.0,
    # Peak fan-out is MAX_SIGNALS(10) × MAX_TRACES(6) = 60. Bump the pool
    # above that so asyncio.gather doesn't queue on the transport.
    limits=httpx.Limits(max_connections=80, max_keepalive_connections=30),
)


def sanitize_df(df: pd.DataFrame) -> pd.DataFrame:
    """Replace NaN/Inf with None for JSON serialization."""
    return df.where(df.notna(), None)


async def lake_query(sql: str) -> pd.DataFrame:
    """POST a SQL string to QuixLake /query, parse the CSV reply.

    Raises HTTPException(502) on non-200 lake responses. RuntimeError if
    required env vars aren't set — fail loud at the edge.
    """
    if not config.QUIXLAKE_URL or not config.QUIX_LAKE_TOKEN:
        missing = [
            name
            for name, val in (
                ("QUIXLAKE_URL", config.QUIXLAKE_URL),
                ("QUIX_LAKE_TOKEN", config.QUIX_LAKE_TOKEN),
            )
            if not val
        ]
        raise RuntimeError(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Set them in .env before starting the service."
        )
    r = await _lake_http.post(
        f"{config.QUIXLAKE_URL}/query",
        content=sql,
        headers=config.lake_headers(),
    )
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Data lake returned {r.status_code} {r.reason_phrase}",
        )
    return pd.read_csv(io.StringIO(r.text))
