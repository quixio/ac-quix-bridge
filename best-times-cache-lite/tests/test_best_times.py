"""Unit tests for best-times-cache-lite core logic.

Tests cover:
  1. _enrich() returns None for invalid iBestTime (0, INT_MAX, negative).
  2. _enrich() returns None when neither session nor config caches have an entry.
  3. _enrich() returns a correctly assembled dict when caches are populated.
  4. _update_best_lap() replaces state+board with a faster lap; ignores a slower one.
  5. /best-laps CSV output is sorted fastest-first and has the correct columns.

No Kafka, no RocksDB, no HTTP calls needed — all module-level dicts are
manipulated directly.
"""
from __future__ import annotations

import os
import sys
import time
import threading
from typing import Any

# Make the parent directory importable so ``import main`` resolves main.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Importing main.py is safe: Application() lives inside main(), module-level
# code only registers FastAPI routes and reads env vars with defaults.
import main as m
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockState:
    """Minimal stand-in for QuixStreams State (no RocksDB required)."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value


def _reset_module_state() -> None:
    """Clear all module-level mutable state between tests."""
    global _active_experiment_ref  # noqa: F821 — just a test helper
    m._session_cache.clear()
    m._config_cache.clear()
    m._board.clear()
    m._board_envs.clear()
    m._active_experiment = ""


def _populate_caches(hostname: str = "host1") -> None:
    """Populate session + config caches with a valid single-host setup."""
    now = time.time()
    m._session_cache[hostname] = {
        "track": "monza",
        "carModel": "ferrari488",
        "playerName": "alice",
        "updated_epoch": now,
    }
    m._config_cache[hostname] = {
        "experiment": "exp-001",
        "driver": "alice",
        "environment": "leadboard",
        "track": "",
        "carModel": "",
        "updated_epoch": now,
    }


# ---------------------------------------------------------------------------
# 1. _enrich() — invalid iBestTime values
# ---------------------------------------------------------------------------


def test_enrich_best_time_zero_returns_none():
    _reset_module_state()
    _populate_caches()
    result = m._enrich({"iBestTime": 0}, "host1")
    assert result is None, "iBestTime=0 must be rejected"


def test_enrich_best_time_int_max_returns_none():
    _reset_module_state()
    _populate_caches()
    result = m._enrich({"iBestTime": m.INT_MAX}, "host1")
    assert result is None, "iBestTime==INT_MAX must be rejected"


def test_enrich_best_time_negative_returns_none():
    _reset_module_state()
    _populate_caches()
    result = m._enrich({"iBestTime": -5000}, "host1")
    assert result is None, "negative iBestTime must be rejected"


def test_enrich_best_time_above_int_max_returns_none():
    _reset_module_state()
    _populate_caches()
    result = m._enrich({"iBestTime": m.INT_MAX + 1}, "host1")
    assert result is None, "iBestTime > INT_MAX must be rejected"


# ---------------------------------------------------------------------------
# 2. _enrich() — no cache entries
# ---------------------------------------------------------------------------


def test_enrich_empty_caches_returns_none():
    _reset_module_state()
    # Both caches empty — no track/carModel/experiment/driver available.
    result = m._enrich({"iBestTime": 90_000}, "host1")
    assert result is None


def test_enrich_config_only_no_track_returns_none():
    _reset_module_state()
    # Config cache populated (experiment + driver) but no session cache,
    # so track and carModel cannot be resolved.
    m._config_cache["host1"] = {
        "experiment": "exp-001",
        "driver": "alice",
        "environment": "leadboard",
        "track": "",
        "carModel": "",
        "updated_epoch": time.time(),
    }
    result = m._enrich({"iBestTime": 90_000}, "host1")
    assert result is None


def test_enrich_session_only_no_experiment_returns_none():
    _reset_module_state()
    # Session cache populated (track + carModel) but no config cache,
    # so experiment cannot be resolved.
    m._session_cache["host1"] = {
        "track": "monza",
        "carModel": "ferrari488",
        "playerName": "alice",
        "updated_epoch": time.time(),
    }
    result = m._enrich({"iBestTime": 90_000}, "host1")
    assert result is None


# ---------------------------------------------------------------------------
# 3. _enrich() — fully populated caches
# ---------------------------------------------------------------------------


def test_enrich_returns_correct_dict():
    _reset_module_state()
    _populate_caches()
    result = m._enrich({"iBestTime": 92_123}, "host1")
    assert result is not None
    assert result["experiment"] == "exp-001"
    assert result["environment"] == "leadboard"
    assert result["track"] == "monza"
    assert result["carModel"] == "ferrari488"
    assert result["driver"] == "alice"
    assert result["iBestTime"] == 92_123


def test_enrich_payload_fields_override_cache():
    """Fields already on the raw payload (replay path) win over caches."""
    _reset_module_state()
    _populate_caches()
    result = m._enrich(
        {
            "track": "nurburgring",
            "carModel": "porsche992",
            "driver": "bob",
            "experiment": "exp-replay",
            "environment": "prod",
            "iBestTime": 88_000,
        },
        "host1",
    )
    assert result is not None
    assert result["track"] == "nurburgring"
    assert result["carModel"] == "porsche992"
    assert result["driver"] == "bob"
    assert result["experiment"] == "exp-replay"
    assert result["iBestTime"] == 88_000


# ---------------------------------------------------------------------------
# 4. _update_best_lap() — state + board update logic
# ---------------------------------------------------------------------------


def _make_val(best_ms: int = 90_000) -> dict[str, Any]:
    return {
        "experiment": "exp-001",
        "environment": "leadboard",
        "track": "monza",
        "carModel": "ferrari488",
        "driver": "alice",
        "iBestTime": best_ms,
    }


def test_update_best_lap_sets_initial_state():
    _reset_module_state()
    state = MockState()
    val = _make_val(90_000)
    m._update_best_lap(val, state)

    key = "exp-001|monza|ferrari488|alice"
    assert state.get(key) == 90_000
    assert m._board["exp-001"]["monza"]["ferrari488"]["alice"] == 90_000


def test_update_best_lap_faster_replaces():
    _reset_module_state()
    state = MockState()
    m._update_best_lap(_make_val(90_000), state)
    m._update_best_lap(_make_val(85_000), state)

    key = "exp-001|monza|ferrari488|alice"
    assert state.get(key) == 85_000
    assert m._board["exp-001"]["monza"]["ferrari488"]["alice"] == 85_000


def test_update_best_lap_slower_does_not_replace():
    _reset_module_state()
    state = MockState()
    m._update_best_lap(_make_val(90_000), state)
    m._update_best_lap(_make_val(95_000), state)  # slower — should be ignored

    key = "exp-001|monza|ferrari488|alice"
    assert state.get(key) == 90_000, "slower lap must not overwrite state"
    assert m._board["exp-001"]["monza"]["ferrari488"]["alice"] == 90_000


def test_update_best_lap_equal_time_not_replaced():
    _reset_module_state()
    state = MockState()
    m._update_best_lap(_make_val(90_000), state)
    # Track _board_set calls by watching _board before/after.
    old_board_val = m._board["exp-001"]["monza"]["ferrari488"]["alice"]
    m._update_best_lap(_make_val(90_000), state)  # same time — should not replace
    assert m._board["exp-001"]["monza"]["ferrari488"]["alice"] == old_board_val


def test_update_best_lap_multiple_drivers_independent():
    _reset_module_state()
    state = MockState()
    val_alice = _make_val(90_000)
    val_bob = {**_make_val(80_000), "driver": "bob"}
    m._update_best_lap(val_alice, state)
    m._update_best_lap(val_bob, state)

    assert m._board["exp-001"]["monza"]["ferrari488"]["alice"] == 90_000
    assert m._board["exp-001"]["monza"]["ferrari488"]["bob"] == 80_000


# ---------------------------------------------------------------------------
# 5. /best-laps — CSV output format, sorted fastest-first
# ---------------------------------------------------------------------------

client = TestClient(m.app_http)


def test_best_laps_csv_sorted_fastest_first():
    _reset_module_state()
    state = MockState()
    # Alice is slower, Bob is faster.
    val_alice = _make_val(95_000)
    val_bob = {**_make_val(90_000), "driver": "bob"}
    m._update_best_lap(val_alice, state)
    m._update_best_lap(val_bob, state)

    resp = client.get("/best-laps?experiment=exp-001")
    assert resp.status_code == 200
    lines = [ln for ln in resp.text.strip().splitlines() if ln]
    assert lines[0] == "environment,experiment,track,carModel,driver,iBestTime"
    data = lines[1:]
    assert len(data) == 2
    times = [int(row.split(",")[-1]) for row in data]
    assert times == sorted(times), "rows must be sorted fastest-first"
    assert times[0] == 90_000  # Bob first
    assert times[1] == 95_000  # Alice second


def test_best_laps_csv_columns():
    _reset_module_state()
    state = MockState()
    m._update_best_lap(_make_val(90_000), state)

    resp = client.get("/best-laps?experiment=exp-001")
    assert resp.status_code == 200
    header = resp.text.strip().splitlines()[0]
    assert header == "environment,experiment,track,carModel,driver,iBestTime"


def test_best_laps_json_format():
    _reset_module_state()
    state = MockState()
    m._update_best_lap(_make_val(90_000), state)

    resp = client.get("/best-laps?experiment=exp-001&format=json")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["iBestTime"] == 90_000
    assert row["driver"] == "alice"


def test_best_laps_empty_board_returns_header_only():
    _reset_module_state()
    resp = client.get("/best-laps?experiment=exp-001")
    assert resp.status_code == 200
    lines = [ln for ln in resp.text.strip().splitlines() if ln]
    assert lines == ["environment,experiment,track,carModel,driver,iBestTime"]


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
