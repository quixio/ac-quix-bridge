"""Tests for /api/sessions.

The endpoint hits the Iceberg catalog /manifest endpoint once, dedupes
distinct (env, rig, exp, driver, track, carModel, session_id) combinations,
collects lap numbers per session, applies client-side filters from query
params, and returns the result.

Tests mock `partition_walker._http_client` with an httpx.MockTransport that
returns a canned catalog manifest body — a JSON object with an `entries`
array where each entry has a `partition_values` dict (one entry per
Parquet file).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

import config
import partition_walker

PART_COLS = [
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
]


def _entry(**partition_values: str | int) -> dict:
    """Build a manifest entry — the catalog returns one per Parquet file."""
    return {"partition_values": partition_values}


def _manifest_body(*entries: dict) -> str:
    return json.dumps({"entries": list(entries)})


@pytest.fixture
def stub_catalog(monkeypatch: pytest.MonkeyPatch):
    """Stub `partition_walker._http_client` with a MockTransport.

    Default state: env vars look set, manifest is empty. Tests can call
    `stub.set_manifest(*entries)` to define the lake shape, or
    `stub.set_response(status, body)` to test error paths.
    """
    state = {"status": 200, "body": _manifest_body()}

    def transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(state["status"], text=state["body"])

    monkeypatch.setattr(config, "CATALOG_URL", "https://catalog.example.com")
    monkeypatch.setattr(config, "CATALOG_TOKEN", "test-token")
    monkeypatch.setattr(config, "TABLE_NAME", "ac_telemetry")
    monkeypatch.setattr(
        partition_walker,
        "_http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )

    class Stub:
        def set_manifest(self, *entries: dict) -> None:
            state["status"] = 200
            state["body"] = _manifest_body(*entries)

        def set_response(self, status: int, body: str = "") -> None:
            state["status"] = status
            state["body"] = body

    return Stub()


@pytest.fixture
def _require_catalog_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set CATALOG_URL + CATALOG_TOKEN so the env-var guard inside
    `_list_session_combinations` doesn't short-circuit before the mock
    transport fires. Used by error-path tests that only mock the transport.
    """
    monkeypatch.setattr(config, "CATALOG_URL", "https://catalog.example.com")
    monkeypatch.setattr(config, "CATALOG_TOKEN", "test-token")


def _full_entry(values: dict[str, str], lap: int | None = None) -> dict:
    pv = {col: values[col] for col in PART_COLS if col in values}
    if lap is not None:
        pv["lap"] = str(lap)
    return _entry(**pv)


def test_empty_manifest_returns_empty_sessions(stub_catalog, client) -> None:
    stub_catalog.set_manifest()
    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": []}


def test_single_session_yields_one_row(stub_catalog, client) -> None:
    ctx = {
        "environment": "prague_office",
        "test_rig": "g29",
        "experiment": "VideoStartSeek",
        "driver": "ludvik",
        "track": "ks_nurburgring",
        "carModel": "bmw_1m",
        "session_id": "2026-04-14T14:56:28.037Z",
    }
    stub_catalog.set_manifest(
        _full_entry(ctx, lap=1),
        _full_entry(ctx, lap=2),
        _full_entry(ctx, lap=3),
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json() == {"sessions": [{**ctx, "laps": [1, 2, 3]}]}


def test_session_with_no_lap_partition_has_empty_laps_list(stub_catalog, client) -> None:
    ctx = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(_full_entry(ctx))  # no lap value

    response = client.get("/api/sessions")
    assert response.status_code == 200
    assert response.json()["sessions"][0]["laps"] == []


def test_laps_sorted_numerically(stub_catalog, client) -> None:
    ctx = {c: "v" for c in PART_COLS}
    # Laps provided in non-sorted order including a two-digit one
    stub_catalog.set_manifest(
        _full_entry(ctx, lap=3),
        _full_entry(ctx, lap=1),
        _full_entry(ctx, lap=10),
        _full_entry(ctx, lap=2),
    )
    response = client.get("/api/sessions")
    assert response.json()["sessions"][0]["laps"] == [1, 2, 3, 10]


def test_multiple_sessions_distinct_environments(stub_catalog, client) -> None:
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry({**base, "environment": "prague_office"}, lap=1),
        _full_entry({**base, "environment": "quix_office"}, lap=1),
    )

    response = client.get("/api/sessions")
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 2
    assert {s["environment"] for s in sessions} == {"prague_office", "quix_office"}


def test_branching_at_experiment_level(stub_catalog, client) -> None:
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry({**base, "experiment": "A"}, lap=1),
        _full_entry({**base, "experiment": "B"}, lap=1),
    )

    response = client.get("/api/sessions")
    sessions = response.json()["sessions"]
    assert len(sessions) == 2
    assert {s["experiment"] for s in sessions} == {"A", "B"}


def test_entries_for_same_session_dedupe_to_single_row(stub_catalog, client) -> None:
    """Multiple manifest entries with identical partition_values (apart from
    lap) collapse into one session — the manifest typically has one entry per
    Parquet file, and a single lap can produce multiple files."""
    ctx = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry(ctx, lap=1),
        _full_entry(ctx, lap=1),  # duplicate file for the same lap
        _full_entry(ctx, lap=2),
    )
    response = client.get("/api/sessions")
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["laps"] == [1, 2]  # lap=1 deduped within the session


def test_filter_narrows_to_matching_environment(stub_catalog, client) -> None:
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry({**base, "environment": "e1"}, lap=1),
        _full_entry({**base, "environment": "e2"}, lap=1),
    )

    response = client.get("/api/sessions?environment=e1")
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["environment"] == "e1"


def test_multiple_filters_narrow_further(stub_catalog, client) -> None:
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry({**base, "environment": "e1", "experiment": "a"}, lap=1),
        _full_entry({**base, "environment": "e1", "experiment": "b"}, lap=1),
        _full_entry({**base, "environment": "e2", "experiment": "b"}, lap=1),
    )

    response = client.get("/api/sessions?environment=e1&experiment=b")
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["experiment"] == "b"
    assert sessions[0]["environment"] == "e1"


def test_filter_for_missing_value_yields_empty(stub_catalog, client) -> None:
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(_full_entry({**base, "environment": "e1"}, lap=1))

    response = client.get("/api/sessions?environment=does_not_exist")
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def test_partial_invalid_filter_yields_empty_without_error(stub_catalog, client) -> None:
    """Valid env + invalid test_rig → empty list, not a crash."""
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry({**base, "environment": "prague_office", "test_rig": "g29"}, lap=1)
    )
    response = client.get(
        "/api/sessions?environment=prague_office&test_rig=not_a_real_rig"
    )
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def test_filter_value_with_special_chars_is_safe(stub_catalog, client) -> None:
    """Filter is exact string equality against partition values — values
    containing slashes/equals just don't match. No injection surface."""
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry({**base, "environment": "prague_office"}, lap=1)
    )
    response = client.get("/api/sessions?environment=../../etc/passwd")
    assert response.status_code == 200
    assert response.json()["sessions"] == []


def test_empty_filter_values_equivalent_to_no_filter(stub_catalog, client) -> None:
    """Explicitly empty-string params should NOT filter anything."""
    base = {c: "v" for c in PART_COLS}
    stub_catalog.set_manifest(
        _full_entry({**base, "environment": "e1"}, lap=1),
        _full_entry({**base, "environment": "e2"}, lap=1),
    )

    response = client.get(
        "/api/sessions?environment=&test_rig=&experiment=&driver=&track=&carModel=&session_id="
    )
    assert response.status_code == 200
    envs_returned = {s["environment"] for s in response.json()["sessions"]}
    assert envs_returned == {"e1", "e2"}


def test_session_id_filter_narrows_to_one_session(stub_catalog, client) -> None:
    base = {c: "v" for c in PART_COLS[:-1]}
    stub_catalog.set_manifest(
        _full_entry({**base, "session_id": "s1"}, lap=1),
        _full_entry({**base, "session_id": "s2"}, lap=1),
    )

    response = client.get("/api/sessions?session_id=s2")
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s2"


def test_manifest_call_propagates_runtime_errors(
    monkeypatch: pytest.MonkeyPatch, client
) -> None:
    """Non-HTTP errors (RuntimeError, etc.) surface as 500 with the detail."""
    import main

    async def boom(_filters: dict[str, str] | None = None) -> list[dict]:
        raise RuntimeError("catalog down")

    # main.py uses `from partition_walker import _list_session_combinations`,
    # so its local name is bound at import — patch the local reference, not
    # the partition_walker module attribute.
    monkeypatch.setattr(main, "_list_session_combinations", boom)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    assert "catalog down" in response.json()["detail"]


def test_catalog_500_surfaces_as_502_with_upstream_status(
    monkeypatch: pytest.MonkeyPatch, _require_catalog_env, client
) -> None:
    """When the catalog returns 500, the proxy responds 502 (bad gateway)
    with the real upstream status in the detail."""

    def mock_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    monkeypatch.setattr(
        partition_walker,
        "_http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    response = client.get("/api/sessions")
    assert response.status_code == 502
    assert "500" in response.json()["detail"]


def test_catalog_403_surfaces_as_502_with_forbidden_detail(
    monkeypatch: pytest.MonkeyPatch, _require_catalog_env, client
) -> None:
    def mock_transport(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Access Forbidden")

    monkeypatch.setattr(
        partition_walker,
        "_http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    response = client.get("/api/sessions")
    assert response.status_code == 502
    assert "403" in response.json()["detail"]
    assert "Forbidden" in response.json()["detail"]


def test_missing_catalog_url_surfaces_clear_error(
    monkeypatch: pytest.MonkeyPatch, client, caplog
) -> None:
    """If CATALOG_URL isn't configured, the error message must name the
    missing var (not leak a confusing httpx.InvalidURL stack trace)."""
    monkeypatch.setattr(config, "CATALOG_URL", None)
    monkeypatch.setattr(config, "CATALOG_TOKEN", "test-token")
    with caplog.at_level(logging.ERROR, logger="main"):
        response = client.get("/api/sessions")
    assert response.status_code == 500
    assert "CATALOG_URL" in response.json()["detail"]
    assert "CATALOG_URL" in caplog.text


def test_missing_catalog_token_surfaces_clear_error(
    monkeypatch: pytest.MonkeyPatch, client
) -> None:
    monkeypatch.setattr(config, "CATALOG_URL", "https://catalog.example.com")
    monkeypatch.setattr(config, "CATALOG_TOKEN", None)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    assert "CATALOG_TOKEN" in response.json()["detail"]


def test_both_catalog_vars_missing_names_both_in_error(
    monkeypatch: pytest.MonkeyPatch, client
) -> None:
    monkeypatch.setattr(config, "CATALOG_URL", None)
    monkeypatch.setattr(config, "CATALOG_TOKEN", None)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "CATALOG_URL" in detail
    assert "CATALOG_TOKEN" in detail


def test_catalog_timeout_surfaces_as_504(
    monkeypatch: pytest.MonkeyPatch, _require_catalog_env, client
) -> None:
    """Timeouts to the catalog map to 504 Gateway Timeout, not 500."""

    def mock_transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=_request)

    monkeypatch.setattr(
        partition_walker,
        "_http_client",
        httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)),
    )
    response = client.get("/api/sessions")
    assert response.status_code == 504
    assert "timed out" in response.json()["detail"].lower()
