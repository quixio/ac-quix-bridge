"""QuixLake query client for post-race telemetry visualization.

Reads the two auto-injected lakehouse query vars (no fallback) and POSTs raw
SQL to the lake's /query endpoint, returning the CSV reply as a DataFrame.
Missing credentials raise at call time; callers wrap the viz in best-effort
try/except so the report never crashes.
"""

from __future__ import annotations

import io
import logging
import os

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

_QUERY_URL_VAR = "Quix__Lakehouse__Query__Url"
_QUERY_TOKEN_VAR = "Quix__Lakehouse__Query__AuthToken"


def lake_query(sql: str, *, timeout: float = 60.0) -> pd.DataFrame:
    """Run a SELECT against the lake /query endpoint; return the CSV as a DataFrame."""
    url = os.environ.get(_QUERY_URL_VAR)
    token = os.environ.get(_QUERY_TOKEN_VAR)
    if not url or not token:
        raise RuntimeError(
            f"lakehouse query creds unset ({_QUERY_URL_VAR}/{_QUERY_TOKEN_VAR})"
        )
    logger.info("[lake] POST %s/query (%d chars sql)", url, len(sql))
    resp = httpx.post(
        f"{url}/query",
        content=sql.encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
        timeout=timeout,
    )
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    logger.info("[lake] query -> %d rows", len(df))
    return df
