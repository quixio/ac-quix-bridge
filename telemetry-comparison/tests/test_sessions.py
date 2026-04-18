"""Smoke tests for /api/sessions with a stubbed QuixLake client.

These pin the CURRENT behavior before we refactor it. Once the new
partition-browse endpoint ships, this file probably goes away.
"""

from __future__ import annotations

import pandas as pd


def test_sessions_returns_rows_from_query(stub_client, client) -> None:
    stub_client.set_response(
        pd.DataFrame(
            [
                {
                    "environment": "prague_office",
                    "test_rig": "g29",
                    "experiment": "VideoStartSeek",
                    "driver": "ludvik",
                    "track": "ks_nurburgring",
                    "carModel": "bmw_1m",
                    "session_id": "2026-04-14T14:56:28.037Z",
                    "first_ts": 1.0,
                    "last_ts": 2.0,
                    "max_lap": 3,
                    "total_samples": 9,
                }
            ]
        )
    )
    response = client.get("/api/sessions")
    assert response.status_code == 200
    body = response.json()
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["driver"] == "ludvik"


def test_sessions_sql_contains_expected_shape(stub_client, client) -> None:
    stub_client.set_response(pd.DataFrame())
    client.get("/api/sessions")
    sql = stub_client.last_sql or ""
    # Pins the current query shape — this test will fail (good) when we
    # refactor to use the partition-browse endpoint instead.
    assert "GROUP BY" in sql
    assert "session_id" in sql
    assert "LIMIT 50" in sql


def test_sessions_respects_limit_query_param(stub_client, client) -> None:
    stub_client.set_response(pd.DataFrame())
    client.get("/api/sessions?limit=5")
    assert "LIMIT 5" in (stub_client.last_sql or "")


def test_sessions_propagates_query_errors_as_500(stub_factory, client) -> None:
    def boom(_sql: str) -> pd.DataFrame:
        raise RuntimeError("lake timeout")

    stub_factory(boom)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    assert "lake timeout" in response.json()["detail"]
