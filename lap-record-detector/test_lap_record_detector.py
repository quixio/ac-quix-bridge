"""
Unit tests for detect_lap_record.
Uses a simple dict-backed mock for the QuixStreams state object.
No Kafka connection required.
"""

import sys
import os

# Ensure the lap-record-detector directory is on the path so we can import main.py
sys.path.insert(0, os.path.dirname(__file__))

from main import detect_lap_record, INT32_MAX


class MockState:
    """Minimal dict-backed state mock matching the QuixStreams state interface."""

    def __init__(self, data=None):
        self._data = data or {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DRIVER = "Alice"
TRACK = "monza"
CAR = "ks_ferrari_sf15t"


def _row(completed_laps, i_last_time, driver=DRIVER, track=TRACK, car=CAR, **extra):
    return {
        "driver": driver,
        "track": track,
        "carModel": car,
        "completedLaps": completed_laps,
        "iLastTime": i_last_time,
        "session_id": "2026-06-17T11:40:05.123Z",
        "environment": "Silverstone Test Day",
        "test_rig": "Sim Rig 1",
        "experiment": "Experiment A",
        "test_id": "test-001",
        "lastTime": "1:23.456",
        "timestamp_ms": 1750160405123,
        **extra,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_enrichment_returns_none():
    """Row with driver='NA' (enrichment not yet arrived) → None."""
    state = MockState()
    row = _row(completed_laps=1, i_last_time=83456, driver="NA")
    assert detect_lap_record(row, state) is None


def test_first_lap_record_no_previous_best():
    """
    First completed lap with a valid time → record emitted.
    previous_best_ms and improvement_ms should be None (no prior best).
    """
    state = MockState()
    combo = f"{DRIVER}|{TRACK}|{CAR}"

    # Seed state so we have seen completedLaps=0 before (lap in progress)
    state.set(f"last_completed|{combo}", 0)

    row = _row(completed_laps=1, i_last_time=83456)
    result = detect_lap_record(row, state)

    assert result is not None
    assert result["lap_number"] == 1
    assert result["lap_time_ms"] == 83456
    assert result["previous_best_ms"] is None
    assert result["improvement_ms"] is None
    assert result["driver"] == DRIVER
    assert result["track"] == TRACK
    assert result["carModel"] == CAR


def test_second_lap_slower_returns_none():
    """Second lap is slower than the first → no record emitted."""
    state = MockState()
    combo = f"{DRIVER}|{TRACK}|{CAR}"
    state.set(f"last_completed|{combo}", 1)
    state.set(f"best_ms|{combo}", 83456)  # first lap was 83.456 s

    row = _row(completed_laps=2, i_last_time=85000)  # slower
    result = detect_lap_record(row, state)

    assert result is None


def test_third_lap_faster_emits_record():
    """Third lap faster than current best → record with correct improvement_ms."""
    state = MockState()
    combo = f"{DRIVER}|{TRACK}|{CAR}"
    state.set(f"last_completed|{combo}", 2)
    state.set(f"best_ms|{combo}", 83456)

    new_time = 82100
    row = _row(completed_laps=3, i_last_time=new_time)
    result = detect_lap_record(row, state)

    assert result is not None
    assert result["lap_time_ms"] == new_time
    assert result["previous_best_ms"] == 83456
    assert result["improvement_ms"] == 83456 - new_time  # 1356


def test_sentinel_zero_returns_none():
    """iLastTime=0 (AC sentinel for no valid time) → None."""
    state = MockState()
    combo = f"{DRIVER}|{TRACK}|{CAR}"
    state.set(f"last_completed|{combo}", 0)

    row = _row(completed_laps=1, i_last_time=0)
    assert detect_lap_record(row, state) is None


def test_sentinel_int32_max_returns_none():
    """iLastTime=INT32_MAX (ACC sentinel for missing lap time) → None."""
    state = MockState()
    combo = f"{DRIVER}|{TRACK}|{CAR}"
    state.set(f"last_completed|{combo}", 0)

    row = _row(completed_laps=1, i_last_time=INT32_MAX)
    assert detect_lap_record(row, state) is None


def test_completed_laps_not_incremented_returns_none():
    """completedLaps same as last call (mid-lap) → None."""
    state = MockState()
    combo = f"{DRIVER}|{TRACK}|{CAR}"
    state.set(f"last_completed|{combo}", 1)

    # completedLaps still 1, no boundary crossed
    row = _row(completed_laps=1, i_last_time=83456)
    assert detect_lap_record(row, state) is None
