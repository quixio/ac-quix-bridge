"""Minimal synchronous Lakehouse Query API client.

Replaces the opaque ``quixlake-sdk`` / ``QuixLakeClient`` used in the
leaderboard routes. The Lakehouse Query API contract (confirmed by
``dev-planning/lakehouse-migration/spec.md §9`` probe):

* ``POST {base_url}/api/query?union_by_name=true``
* ``Authorization: Bearer <token>``
* ``Content-Type: text/plain``
* Request body: raw SQL string
* Response: ``text/csv`` (HTTP 200 — always, even on query errors)
* Error shape: HTTP 200, body starts with ``\\n# ERROR: <DuckDB message>``

One ``httpx.Client`` per query call — no persistent connection pool.
Queries are made in the context of a single HTTP request lifecycle so
there is no benefit to sharing a long-lived client across requests.

# TODO: create docs/architecture-leaderboard-*.md
# (see dev-planning/leaderboard-consolidated/spec.md)
"""

from __future__ import annotations

import io

import httpx
import pandas as pd


class LakehouseQueryError(Exception):
    """Raised when the Lakehouse engine returns an error body.

    The Lakehouse Query API always returns HTTP 200; a query error is
    signalled by a response body that starts with ``\\n# ERROR:``.
    """


class LakehouseClient:
    """Synchronous HTTP client for the Quix Lakehouse Query API."""

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def query(self, sql: str) -> pd.DataFrame:
        """POST *sql* to ``{base_url}/api/query?union_by_name=true``.

        Returns a :class:`pandas.DataFrame` parsed from the ``text/csv``
        response body.

        Raises :class:`LakehouseQueryError` when the response body starts
        with ``\\n# ERROR:`` (the Lakehouse engine's SQL error shape).

        Raises :class:`httpx.HTTPStatusError` for non-200 HTTP responses
        (transport-level failures).
        """
        # Endpoint shape matches the production QuixLake API on box Cloud
        # (`https://quixlake-api-…edge.byox.demo/query`). The new-format
        # Lakehouse Query API at `/api/query?union_by_name=true` was the
        # original migration target, but the deployed value of `LAKE_API_URL`
        # still points at QuixLake, so we use the QuixLake path here.
        url = f"{self._base_url}/query"
        # TODO(ssl): verify=False — demo Box Cloud uses a self-signed cert;
        # remove when production TLS certificates are provisioned on this
        # deployment.
        with httpx.Client(verify=False) as client:
            r = client.post(
                url,
                content=sql,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "text/plain",
                },
                timeout=30.0,
            )
        r.raise_for_status()
        body = r.text
        if body.lstrip("\n").startswith("# ERROR:"):
            raise LakehouseQueryError(body.strip())
        return pd.read_csv(io.StringIO(body))
