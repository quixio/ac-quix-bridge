"""Tests for the new /api/partition-values behavior.

The endpoint is refactored to use QuixLake's native /partitions HTTP endpoint
instead of SQL DISTINCT. Each call is a single S3 directory listing (~150ms)
rather than a full Parquet scan (seconds).

The tests monkeypatch the new helper function main._list_partition_children
so we can run without a real lake.
"""

from __future__ import annotations

from typing import Any

import pytest

import main


@pytest.fixture
def stub_partitions(monkeypatch: pytest.MonkeyPatch):
    """Return a recorder that captures calls to _list_partition_children and
    returns canned responses keyed by the path prefix.

    Usage:
        def test_x(stub_partitions, client):
            stub_partitions.set({"": ["environment=prague_office"]})
            r = client.get("/api/partition-values?column=environment")
    """

    class Recorder:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self._responses: dict[str, list[str]] = {}

        def set(self, mapping: dict[str, list[str]]) -> None:
            self._responses = mapping

        def __call__(self, path: str) -> list[str]:
            self.calls.append(path)
            return self._responses.get(path, [])

    recorder = Recorder()
    monkeypatch.setattr(main, "_list_partition_children", recorder)
    return recorder


def test_top_level_environments_no_upstream(stub_partitions, client) -> None:
    stub_partitions.set(
        {"": ["environment=prague_office", "environment=quix_office"]}
    )
    response = client.get("/api/partition-values?column=environment")
    assert response.status_code == 200
    assert response.json() == {"values": ["prague_office", "quix_office"]}
    assert stub_partitions.calls == [""]


def test_test_rigs_for_a_specific_environment(stub_partitions, client) -> None:
    stub_partitions.set(
        {"environment=prague_office": ["test_rig=g29", "test_rig=t300"]}
    )
    response = client.get(
        "/api/partition-values?column=test_rig&environment=prague_office"
    )
    assert response.status_code == 200
    assert response.json() == {"values": ["g29", "t300"]}
    assert stub_partitions.calls == ["environment=prague_office"]


def test_session_ids_for_full_prefix(stub_partitions, client) -> None:
    full_path = (
        "environment=prague_office/test_rig=g29/experiment=VideoStartSeek/"
        "driver=ludvik/track=ks_nurburgring/carModel=bmw_1m"
    )
    stub_partitions.set(
        {full_path: ["session_id=2026-04-14T14:56:28.037Z"]}
    )
    response = client.get(
        "/api/partition-values?column=session_id"
        "&environment=prague_office&test_rig=g29"
        "&experiment=VideoStartSeek&driver=ludvik"
        "&track=ks_nurburgring&carModel=bmw_1m"
    )
    assert response.status_code == 200
    assert response.json() == {"values": ["2026-04-14T14:56:28.037Z"]}


def test_ignores_downstream_filters(stub_partitions, client) -> None:
    # If the frontend sends filters for columns AFTER the requested one,
    # those must be ignored — otherwise the path would be malformed.
    stub_partitions.set(
        {"environment=prague_office": ["test_rig=g29"]}
    )
    response = client.get(
        "/api/partition-values?column=test_rig"
        "&environment=prague_office"
        "&driver=ludvik"  # downstream of test_rig — should be ignored
    )
    assert response.status_code == 200
    assert response.json() == {"values": ["g29"]}
    # Path must NOT contain driver=ludvik
    assert stub_partitions.calls == ["environment=prague_office"]


def test_empty_result_returns_empty_values(stub_partitions, client) -> None:
    stub_partitions.set({"environment=prague_office": []})
    response = client.get(
        "/api/partition-values?column=test_rig&environment=prague_office"
    )
    assert response.status_code == 200
    assert response.json() == {"values": []}


def test_invalid_column_returns_400(stub_partitions, client) -> None:
    response = client.get("/api/partition-values?column=bogus")
    assert response.status_code == 400
    assert stub_partitions.calls == []  # no lake call made


def test_missing_upstream_returns_400(stub_partitions, client) -> None:
    # Requesting test_rig without environment should reject, because the
    # partition tree is strict: you can't list rigs without knowing which
    # environment's rigs.
    response = client.get("/api/partition-values?column=test_rig")
    assert response.status_code == 400
    assert stub_partitions.calls == []


def test_uses_native_partition_helper_not_sql(
    stub_partitions, stub_client, client
) -> None:
    # Paranoia test: make sure the refactored endpoint never falls back to
    # running SQL. If stub_client.last_sql gets populated, something still
    # calls get_client().query() — the refactor isn't complete.
    stub_partitions.set({"": ["environment=prague_office"]})
    response = client.get("/api/partition-values?column=environment")
    assert response.status_code == 200
    assert stub_client.last_sql is None
