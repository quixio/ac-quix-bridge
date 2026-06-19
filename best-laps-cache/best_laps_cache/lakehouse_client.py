"""Minimal synchronous Lakehouse Query API client (Arrow-preferred, CSV fallback).

Ported from ``leaderboard-service/api/lakehouse_client.py`` (the proven
byox path). Follows the ``quix-lakehouse-access`` skill contract:

* ``POST {base_url}/query``
* ``Authorization: Bearer <token>``
* ``Content-Type: text/plain``  (request body is a raw SQL string)
* ``Accept: application/vnd.apache.arrow.stream`` — we *request* the Arrow
  IPC stream because it is faster / lower-memory than CSV for the full-table
  seed scan. Arrow support on this endpoint is **unconfirmed**, so the client
  degrades gracefully: if the server ignores the Accept header and returns
  ``text/csv`` (or any non-Arrow body), we parse it with the original CSV path.
* Response (Arrow path): ``Content-Type`` contains ``arrow`` → parse the IPC
  stream with pyarrow into a DataFrame.
* Response (text path): ``text/csv`` (HTTP 200 — always, even on query
  errors). Error shape: HTTP 200, body starts with ``\\n# ERROR: <DuckDB
  message>``. Errors may still arrive as text/plain even when Arrow was
  requested, so the ``# ERROR:`` path stays reachable on the text branch.

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
from dataclasses import dataclass

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# Canonical Apache Arrow IPC *stream* media type (Arrow Flight SQL / Arrow
# spec). Requested via Accept; the server may ignore it and reply with CSV.
_ARROW_ACCEPT = "application/vnd.apache.arrow.stream"

# The five partition columns are strings whose values can look numeric in some
# chunks and not others (e.g. all-digit car/track names). On the CSV path this
# triggers pandas' chunked type inference; on the Arrow path Arrow may infer a
# numeric type. Pin them to ``str`` on both paths so dtypes are stable;
# ``iBestTime`` is left to numeric inference.
_STR_PARTITION_COLS = ("environment", "experiment", "track", "carModel", "driver")

# 3 attempts total: len(_RETRY_BACKOFFS_S) + 1. Worst case wall time per
# query() ≈ 3 × 30 s timeout + (0.5 + 1.0) backoff ≈ 91.5 s — bounded, and
# only on the reconcile daemon thread.
_RETRY_BACKOFFS_S = (0.5, 1.0)
_QUERY_TIMEOUT_S = 30.0


class LakehouseQueryError(Exception):
    """Raised when the Lakehouse engine returns an error body (HTTP 200,
    body starting with ``\\n# ERROR:``)."""


@dataclass(frozen=True)
class _LakeResponse:
    """The bits of an ``httpx.Response`` ``query()`` needs to branch on.

    Carrying a small struct (rather than the live response) keeps the retry
    loop's ``with httpx.Client(...)`` context closed before parsing.
    """

    content_type: str
    content: bytes
    text: str

    @property
    def is_arrow(self) -> bool:
        return "arrow" in self.content_type.lower()


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
        """POST *sql* to ``{base_url}/query`` and parse the response.

        Prefers an Arrow IPC stream (requested via ``Accept``) and parses it
        with pyarrow when the response ``Content-Type`` contains ``arrow``;
        otherwise treats the body as text and runs the CSV / ``# ERROR:`` path.

        Raises :class:`LakehouseQueryError` on a Lakehouse SQL error body,
        or re-raises the last transport exception once the bounded retry
        policy is exhausted.
        """
        url = f"{self._base_url}/query"
        resp = self._post_with_retry(url, sql)
        if resp.is_arrow:
            return self._parse_arrow(resp.content)
        return self._parse_text(resp.text)

    @staticmethod
    def _parse_arrow(content: bytes) -> pd.DataFrame:
        """Decode an Arrow IPC payload into a DataFrame with pinned str dtypes.

        Tries the IPC *stream* reader first (matches the requested
        ``application/vnd.apache.arrow.stream``); falls back to the random-
        access *file* reader if the server sent the file format instead.
        """
        import pyarrow as pa  # local import: only needed on the Arrow path

        if not content:
            return pd.DataFrame()
        try:
            table = pa.ipc.open_stream(io.BytesIO(content)).read_all()
        except pa.lib.ArrowInvalid:
            table = pa.ipc.open_file(io.BytesIO(content)).read_all()
        df = table.to_pandas()
        for col in _STR_PARTITION_COLS:
            if col in df.columns:
                df[col] = df[col].astype(str)
        return df

    @staticmethod
    def _parse_text(body: str) -> pd.DataFrame:
        """Original CSV / ``# ERROR:`` path, unchanged in behaviour."""
        if body.lstrip("\n").startswith("# ERROR:"):
            raise LakehouseQueryError(body.strip())
        if not body.strip():
            return pd.DataFrame()
        # Pin the five partition columns to ``str`` and disable chunked
        # inference so the dtypes are stable and pandas never emits a
        # ``DtypeWarning: Columns ... have mixed types``; ``iBestTime`` is
        # left to numeric inference.
        return pd.read_csv(
            io.StringIO(body),
            low_memory=False,
            dtype=dict.fromkeys(_STR_PARTITION_COLS, str),
        )

    def _post_with_retry(self, url: str, sql: str) -> _LakeResponse:
        attempts = len(_RETRY_BACKOFFS_S) + 1
        last_exc: Exception | None = None
        headers = {"Content-Type": "text/plain", "Accept": _ARROW_ACCEPT}
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
                return _LakeResponse(
                    content_type=r.headers.get("content-type", ""),
                    content=r.content,
                    text=r.text,
                )
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
