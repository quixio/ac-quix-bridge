"""Tests for /api/telemetry.

The endpoint POSTs SQL to QuixLake's /query endpoint via a shared async client
(see main._lake_http) and parses the CSV reply with pandas. Tests stub the
shared client with httpx.MockTransport so no network is hit.

Coverage: response shape, sort guarantee (downsample() depends on sorted x),
signal-name validation, lake error mapping, and the lap-1 trim contract.
"""

from __future__ import annotations

import config

COMMON_PARAMS = {
    "environment": "prague_office",
    "test_rig": "g29",
    "experiment": "VideoSyncFix",
    "driver": "ludvik",
    "track": "ks_nurburgring",
    "carModel": "bmw_1m",
    "session_id": "2026-04-17 06:39:45",
}


def _csv(rows: list[tuple]) -> str:
    """Build a minimal /query CSV reply for: normalizedCarPosition, timestamp_ms, speedKmh."""
    header = "normalizedCarPosition,timestamp_ms,speedKmh"
    lines = [f"{ncp},{ts},{spd}" for ncp, ts, spd in rows]
    return "\n".join([header, *lines]) + "\n"


def test_happy_path_returns_expected_shape(stub_lake, client) -> None:
    stub_lake.set_csv(_csv([(0.1, 100, 50.0), (0.5, 500, 80.0), (0.9, 900, 60.0)]))
    r = client.get("/api/telemetry", params={**COMMON_PARAMS, "lap": 5, "signals": "speedKmh"})
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"] == COMMON_PARAMS["session_id"]
    assert body["lap"] == 5
    assert body["signals"] == ["speedKmh"]
    assert body["count"] == 3
    assert body["data"]["normalizedCarPosition"] == [0.1, 0.5, 0.9]
    assert body["data"]["speedKmh"] == [50.0, 80.0, 60.0]


def test_response_sorted_by_normalized_position(stub_lake, client) -> None:
    """Lake may return rows in any order (we dropped SQL ORDER BY); the
    endpoint must still hand back data sorted on normalizedCarPosition so
    the frontend's downsample() — which walks x by index — renders correctly."""
    stub_lake.set_csv(_csv([(0.9, 900, 60.0), (0.1, 100, 50.0), (0.5, 500, 80.0)]))
    r = client.get("/api/telemetry", params={**COMMON_PARAMS, "lap": 2, "signals": "speedKmh"})
    assert r.status_code == 200
    assert r.json()["data"]["normalizedCarPosition"] == [0.1, 0.5, 0.9]
    assert r.json()["data"]["speedKmh"] == [50.0, 80.0, 60.0]


def test_invalid_signal_name_yields_400(stub_lake, client) -> None:
    """Signal names are interpolated into the SELECT — non-identifiers must
    be rejected before any SQL is built (defense in depth alongside the
    partition-value allowlist)."""
    r = client.get(
        "/api/telemetry", params={**COMMON_PARAMS, "lap": 1, "signals": "speedKmh; DROP TABLE foo;"}
    )
    assert r.status_code == 400
    assert "Invalid signal name" in r.json()["detail"]


def test_lake_error_surfaces_as_502(stub_lake, client) -> None:
    """Non-200 from QuixLake → 502 Bad Gateway with upstream status in detail."""
    stub_lake.set_response(500, body="internal error")
    r = client.get("/api/telemetry", params={**COMMON_PARAMS, "lap": 1, "signals": "speedKmh"})
    assert r.status_code == 502
    assert "500" in r.json()["detail"]


def test_missing_env_var_yields_500_with_var_name(stub_lake, client, monkeypatch) -> None:
    """If QUIXLAKE_URL isn't set, the route must surface a clear error instead
    of leaking an httpx.InvalidURL stack trace."""
    monkeypatch.setattr(config, "QUIXLAKE_URL", None)
    r = client.get("/api/telemetry", params={**COMMON_PARAMS, "lap": 1, "signals": "speedKmh"})
    assert r.status_code == 500
    assert "QUIXLAKE_URL" in r.json()["detail"]


def test_missing_token_only_names_token_in_error(stub_lake, client, monkeypatch) -> None:
    """Mirror of the URL-missing test for the other env var, so a deploy that
    forgets only the token gets a clear hint pointing at the right name."""
    monkeypatch.setattr(config, "QUIX_LAKE_TOKEN", None)
    r = client.get("/api/telemetry", params={**COMMON_PARAMS, "lap": 1, "signals": "speedKmh"})
    assert r.status_code == 500
    assert "QUIX_LAKE_TOKEN" in r.json()["detail"]
    assert "QUIXLAKE_URL" not in r.json()["detail"]


def test_lap_1_race_start_trim_drops_pre_start_samples(stub_lake, client) -> None:
    """Lap 1 has a special trim: when normalizedCarPosition wraps from near 1
    back to near 0 (race start), drop everything before the wrap. Keeps the
    contract for the other dev's video-sync code intact."""
    # ts ascending; normPos walks 0.92 → 0.05 (wrap) → 0.1 → 0.5
    csv_text = "normalizedCarPosition,timestamp_ms,speedKmh\n"
    csv_text += "0.92,100,30\n0.95,200,32\n0.05,300,80\n0.10,400,82\n0.50,500,90\n"
    stub_lake.set_csv(csv_text)
    r = client.get("/api/telemetry", params={**COMMON_PARAMS, "lap": 1, "signals": "speedKmh"})
    assert r.status_code == 200
    body = r.json()
    # Two pre-wrap samples must be dropped; remaining sorted by nCP
    assert body["count"] == 3
    assert body["data"]["normalizedCarPosition"] == [0.05, 0.10, 0.50]


def test_lap_1_pit_start_returns_empty_when_no_full_circuit(stub_lake, client) -> None:
    """Lap 1 pit-start case: nCP only ranges from ~0.7 to ~1.0 with no wrap
    back to 0. That's a pure out-lap with no full-circuit data — the trim
    drops it entirely so the frontend doesn't render a partial line."""
    # All samples in the back half of the lap, no wrap to 0
    csv_text = "normalizedCarPosition,timestamp_ms,speedKmh\n"
    csv_text += "0.72,100,40\n0.80,200,55\n0.90,300,60\n0.95,400,62\n"
    stub_lake.set_csv(csv_text)
    r = client.get("/api/telemetry", params={**COMMON_PARAMS, "lap": 1, "signals": "speedKmh"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["data"]["normalizedCarPosition"] == []
