"""Minimal synchronous Lakehouse Query API client (Arrow-preferred, CSV fallback).

Ported verbatim from ``best-laps-cache/best_laps_cache/lakehouse_client.py`` (which
was itself ported from this service's ``api/lakehouse_client.py``). Follows the
``quix-lakehouse-access`` skill contract:

* ``POST {base_url}/query`` with ``Authorization: Bearer <token>`` and
  ``Content-Type: text/plain`` (request body is a raw SQL string).
* ``Accept: application/vnd.apache.arrow.stream`` — we request the Arrow IPC
  stream; the server may ignore it and return ``text/csv``, which we parse on the
  text path. Errors arrive as HTTP 200 with a body starting ``\\n# ERROR:``.

BYOX uses self-signed certs, so ``httpx.Client`` is built with ``verify=False``.
Transient failures (timeout / transport / 5xx) get a small bounded retry;
deterministic SQL errors and 4xx are not retried. Only the seed path calls this,
on a worker thread — a slow query never touches the SDF or the HTTP loop.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

_ARROW_ACCEPT = "application/vnd.apache.arrow.stream"

# Partition columns whose all-digit values can trip pandas/Arrow numeric
# inference; pin them to ``str`` so dtypes are stable.
_STR_PARTITION_COLS = ("environment", "experiment", "track", "carModel", "driver")

_RETRY_BACKOFFS_S = (0.5, 1.0)
_QUERY_TIMEOUT_S = 30.0


class LakehouseQueryError(Exception):
    """Raised when the Lakehouse engine returns an error body (HTTP 200,
    body starting with ``\\n# ERROR:``)."""


@dataclass(frozen=True)
class _LakeResponse:
    content_type: str
    content: bytes
    text: str

    @property
    def is_arrow(self) -> bool:
        return "arrow" in self.content_type.lower()


def _is_retryable(exc: Exception) -> bool:
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
        url = f"{self._base_url}/query"
        resp = self._post_with_retry(url, sql)
        if resp.is_arrow:
            return self._parse_arrow(resp.content)
        return self._parse_text(resp.text)

    @staticmethod
    def _parse_arrow(content: bytes) -> pd.DataFrame:
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
        if body.lstrip("\n").startswith("# ERROR:"):
            raise LakehouseQueryError(body.strip())
        if not body.strip():
            return pd.DataFrame()
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
                # BYOX uses self-signed certs -> verify=False.
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
