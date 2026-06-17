"""Degraded-mode enrichment fallback tests.

Spec (degraded-mode-fallback): when raw telemetry is flowing but the
DCM/session enrichment can't resolve a driver — e.g. replaying old data with
no live session / DCM experiment config — the active-row build used to drop
EVERY tick at `_record_message`'s `(track, car, driver)` guard, so the live
stream never opened. The fix substitutes a clearly-placeholder driver name
(`FALLBACK_DRIVER_NAME`, default "John Doe") so a degraded row still renders;
track/car gain analogous fallbacks (`FALLBACK_TRACK`/`FALLBACK_CAR`) only when
no session is cached at all.

These tests poke module state directly and capture the enriched payload that
`_handle_raw_message` hands to `_record_message` (monkeypatched), so no Kafka
or lake is required.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-token")
os.environ.setdefault("CONFIG_API_URL", "http://localhost:8001")

from api import live_telemetry  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    """Isolate each test: clear the metadata caches + live/raw state."""
    saved_session_cache = dict(live_telemetry._session_cache)
    saved_experiment_cache = dict(live_telemetry._experiment_cache)
    saved_live = live_telemetry._live_session
    saved_tick = live_telemetry._last_raw_tick_epoch
    saved_no_meta = live_telemetry._no_metadata_logged
    saved_latest_dcm = live_telemetry._latest_dcm_config
    live_telemetry._session_cache.clear()
    live_telemetry._experiment_cache.clear()
    live_telemetry._live_session = None
    live_telemetry._last_raw_tick_epoch = 0.0
    live_telemetry._no_metadata_logged = False
    live_telemetry._latest_dcm_config = None
    try:
        yield
    finally:
        live_telemetry._session_cache.clear()
        live_telemetry._session_cache.update(saved_session_cache)
        live_telemetry._experiment_cache.clear()
        live_telemetry._experiment_cache.update(saved_experiment_cache)
        live_telemetry._live_session = saved_live
        live_telemetry._last_raw_tick_epoch = saved_tick
        live_telemetry._no_metadata_logged = saved_no_meta
        live_telemetry._latest_dcm_config = saved_latest_dcm


def _capture(monkeypatch) -> list[dict]:
    """Replace `_record_message` with a capturing stub; return the captured
    list. The real `_record_message` is exercised separately by the raw-gate
    suite — here we only assert what enrichment HANDS to it (i.e. that the
    tick is no longer dropped before the guard)."""
    captured: list[dict] = []
    monkeypatch.setattr(
        live_telemetry, "_record_message", lambda payload: captured.append(payload)
    )
    return captured


def test_no_driver_falls_back_to_john_doe(monkeypatch):
    """Resolvable track/car (live session) but NO DCM driver and empty
    playerName → active row built with driver='John Doe' (not dropped)."""
    now = time.time()
    live_telemetry._session_cache["QUIX-GAMING_1"] = {
        "track": "nurburgring",
        "carModel": "ferrari_488",
        "playerName": "",  # no AC player name either
        "updated_epoch": now,
    }
    # No experiment-cache entry carries a driver → DCM resolution is empty.

    captured = _capture(monkeypatch)
    live_telemetry._handle_raw_message(
        "QUIX-GAMING_1",
        {
            "track": None,
            "carModel": None,
            "iCurrentTime": 12345,
            "completedLaps": 1,
            "normalizedCarPosition": 0.42,
            "experiment": "exp-1",  # set so enrichment skips the lake resolver
        },
    )

    assert len(captured) == 1, "tick must reach _record_message, not be dropped"
    enriched = captured[0]
    assert enriched["track"] == "nurburgring"
    assert enriched["carModel"] == "ferrari_488"
    assert enriched["driver"] == "John Doe"


def test_resolved_driver_wins_over_fallback(monkeypatch):
    """A fully-resolved tick keeps the real DCM driver — fallback never
    masks a genuine resolution."""
    now = time.time()
    live_telemetry._session_cache["QUIX-GAMING_1"] = {
        "track": "nurburgring",
        "carModel": "ferrari_488",
        "playerName": "PlayerOne",
        "updated_epoch": now,
    }
    live_telemetry._experiment_cache["Walter"] = {
        "experiment": "exp-1",
        "driver": "LordSiderius",
        "environment": "track",
        "fetched_epoch": now,
        "updated_epoch": now,
    }

    captured = _capture(monkeypatch)
    live_telemetry._handle_raw_message(
        "QUIX-GAMING_1",
        {
            "track": None,
            "carModel": None,
            "iCurrentTime": 100,
            "completedLaps": 0,
            "normalizedCarPosition": 0.1,
            "experiment": "exp-1",
        },
    )

    assert len(captured) == 1
    assert captured[0]["driver"] == "LordSiderius"
    assert captured[0]["driver"] != "John Doe"


def test_no_session_uses_track_car_driver_fallbacks(monkeypatch):
    """No session cached at all → degraded fallback session (Unknown track/car)
    + John Doe driver so a row still appears instead of dropping every tick."""
    captured = _capture(monkeypatch)
    live_telemetry._handle_raw_message(
        "QUIX-GAMING_1",
        {
            "track": None,
            "carModel": None,
            "iCurrentTime": 1,
            "completedLaps": 0,
            "normalizedCarPosition": 0.0,
            "experiment": "exp-1",
        },
    )

    assert len(captured) == 1, "with fallbacks the no-session tick is not dropped"
    enriched = captured[0]
    assert enriched["track"] == "Unknown"
    assert enriched["carModel"] == "Unknown"
    assert enriched["driver"] == "John Doe"


def test_latest_dcm_config_beats_fallback(monkeypatch):
    """Per-hostname resolution fully EMPTY (no session cache, no experiment
    cache) but the hostname-agnostic latest DCM config HAS an experiment +
    session config → live row enriched with the DCM driver/track/car, NOT the
    John Doe / Unknown placeholders.

    This is the byox case: the experiment config's `target_key` is a driver
    name that never matches the live tick's hostname, so the per-hostname
    `_experiment_cache` stays empty and the "last config message" path is the
    only source of real enrichment before the placeholder fallback.
    """
    # `_fetch_latest_dcm_config` is the network boundary; stub it so the
    # TTL-cache wrapper (`_get_latest_dcm_config`) exercises its real logic.
    monkeypatch.setattr(
        live_telemetry,
        "_fetch_latest_dcm_config",
        lambda: {
            "driver": "littlemermaid",
            "experiment": "ConferencePrague",
            "track": "Spa",
            "car": "porsche_991ii_gt3_r",
            "environment": "prague_office",
        },
    )

    captured = _capture(monkeypatch)
    live_telemetry._handle_raw_message(
        "QUIX-GAMING",  # hostname matches no DCM target_key
        {
            "track": None,
            "carModel": None,
            "iCurrentTime": 4242,
            "completedLaps": 0,
            "normalizedCarPosition": 0.33,
            "experiment": "exp-1",  # set so enrichment skips the lake resolver
        },
    )

    assert len(captured) == 1, "tick must reach _record_message, not be dropped"
    enriched = captured[0]
    assert enriched["track"] == "Spa"
    assert enriched["carModel"] == "porsche_991ii_gt3_r"
    assert enriched["driver"] == "littlemermaid"
    assert enriched["driver"] != "John Doe"
    assert enriched["track"] != "Unknown"
