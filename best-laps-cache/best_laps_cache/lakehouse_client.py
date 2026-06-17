"""Minimal synchronous Lakehouse Query API client.

Ported from ``leaderboard-service/api/lakehouse_client.py`` (the proven
byox path). Follows the ``quix-lakehouse-access`` skill contract:

* ``POST {base_url}/query``
* ``Authorization: Bearer <token>``
* ``Content-Type: text/plain``  (request body is a raw SQL string)
* Response: ``text/csv`` (HTTP 200 — always, even on query errors)
* Error shape: HTTP 200, body starts with ``\\n# ERROR: <DuckDB message>``

byox uses self-signed certs throughout, so the ``httpx.Client`` is built
with ``verify=False``. Transient failures (timeout / transport / 5xx) get a
small bounded retry; deterministic SQL errors and 4xx are not retried.

The reconcile worker is the only caller, on its own daemon thread — a slow
or hung query here never touches the Kafka consumer or the HTTP loop.
"""

from __future__ import annotations

import io
import logging
import time

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# 3 attempts total: len(_RETRY_BACKOFFS_S) + 1. Worst case wall time per
# query() ≈ 3 × 30 s timeout + (0.5 + 1.0) backoff ≈ 91.5 s — bounded, and
# only on the reconcile daemon thread.
_RETRY_BACKOFFS_S = (0.5, 1.0)
_QUERY_TIMEOUT_S = 30.0


class LakehouseQueryError(Exception):
    """Raised when the Lakehouse engine returns an error body (HTTP 200,
    body starting with ``\\n# ERROR:``)."""


def _is_retryable(exc: Exception) -> bool:
    """True for transient lake failures worth a bounded retry."""
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


class LakehouseClient:
    """Synchronous HTTP client for the Quix Lakehouse Query API."""

    def __init__(self, base_url: str, token: str | None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def query(self, sql: str) -> pd.DataFrame:
        """POST *sql* to ``{base_url}/query`` and parse the CSV response.

        Raises :class:`LakehouseQueryError` on a Lakehouse SQL error body,
        or re-raises the last transport exception once the bounded retry
        policy is exhausted.
        """
        url = f"{self._base_url}/query"
        body = self._post_with_retry(url, sql)
        if body.lstrip("\n").startswith("# ERROR:"):
            raise LakehouseQueryError(body.strip())
        if not body.strip():
            return pd.DataFrame()
        # The five partition columns are strings whose values can look numeric
        # in some chunks and not others (e.g. all-digit car/track names),
        # which makes pandas' chunked type inference emit a
        # ``DtypeWarning: Columns ... have mixed types``. Pin them to ``str``
        # and disable chunked inference so the dtypes are stable and the
        # warning never fires; ``iBestTime`` is left to numeric inference.
        return pd.read_csv(
            io.StringIO(body),
            low_memory=False,
            dtype={
                "environment": str,
                "experiment": str,
                "track": str,
                "carModel": str,
                "driver": str,
            },
        )

    def _post_with_retry(self, url: str, sql: str) -> str:
        attempts = len(_RETRY_BACKOFFS_S) + 1
        last_exc: Exception | None = None
        headers = {"Content-Type": "text/plain"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        for attempt in range(attempts):
            try:
                # byox uses self-signed certs → verify=False.
                with httpx.Client(verify=False) as client:
                    r = client.post(
                        url,
                        content=sql,
                        headers=headers,
                        timeout=_QUERY_TIMEOUT_S,
                    )
                r.raise_for_status()
                return r.text
            except Exception as exc:  # noqa: BLE001 — classified below
                last_exc = exc
                if not _is_retryable(exc) or attempt == attempts - 1:
                    raise
                backoff = _RETRY_BACKOFFS_S[attempt]
                logger.warning(
                    "lake query transient failure (attempt %d/%d): %s: %s — "
                    "retrying in %.1fs",
                    attempt + 1,
                    attempts,
                    type(exc).__name__,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
        assert last_exc is not None
        raise last_exc
