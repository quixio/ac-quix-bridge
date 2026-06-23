"""Smoke tests for BestLapsMirror + pipeline fold behaviour.

Covers:
1. BestLapsMirror.update + get works correctly
2. fold_lap dedup: same driver/track/car, better time updates, worse time is ignored
3. to_rows returns exactly one row per driver/track/car combo
"""

from __future__ import annotations

import threading

import pytest

from best_laps_cache.mirror import BestLapsMirror
from best_laps_cache.state_model import fold_lap, to_rows


# ---------------------------------------------------------------------------
# BestLapsMirror basic operations
# ---------------------------------------------------------------------------


def test_mirror_update_and_get():
    mirror = BestLapsMirror()
    payload = {"_env": "rig", "trk": {"car": {"Ada": 90000}}}
    mirror.update("exp1", payload)
    assert mirror.get("exp1") == payload


def test_mirror_get_unknown_returns_none():
    mirror = BestLapsMirror()
    assert mirror.get("nonexistent") is None


def test_mirror_experiments_lists_keys():
    mirror = BestLapsMirror()
    mirror.update("expA", {"_env": "rig"})
    mirror.update("expB", {"_env": "rig"})
    assert set(mirror.experiments()) == {"expA", "expB"}


def test_mirror_update_overwrites():
    mirror = BestLapsMirror()
    mirror.update("exp1", {"_env": "rig", "trk": {"car": {"Ada": 90000}}})
    mirror.update("exp1", {"_env": "rig", "trk": {"car": {"Ada": 88000}}})
    assert mirror.get("exp1")["trk"]["car"]["Ada"] == 88000


def test_mirror_thread_safe_concurrent_updates():
    """Multiple threads can update and get without error."""
    mirror = BestLapsMirror()
    errors: list[Exception] = []

    def worker(experiment: str, ms: int) -> None:
        try:
            for _ in range(50):
                mirror.update(experiment, {"_env": "rig", "trk": {"car": {"d": ms}}})
                mirror.get(experiment)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"exp{i}", i * 1000)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


# ---------------------------------------------------------------------------
# fold_lap dedup behaviour
# ---------------------------------------------------------------------------


def test_fold_lap_better_time_updates():
    payload, changed = fold_lap(None, "trk", "car", "Ada", 90000, environment="rig")
    assert changed is True
    assert payload["trk"]["car"]["Ada"] == 90000

    payload2, changed2 = fold_lap(payload, "trk", "car", "Ada", 88000, environment="rig")
    assert changed2 is True
    assert payload2["trk"]["car"]["Ada"] == 88000


def test_fold_lap_worse_time_ignored():
    payload, _ = fold_lap(None, "trk", "car", "Ada", 90000, environment="rig")
    payload2, changed = fold_lap(payload, "trk", "car", "Ada", 95000, environment="rig")
    assert changed is False
    assert payload2["trk"]["car"]["Ada"] == 90000


def test_fold_lap_same_time_not_changed():
    payload, _ = fold_lap(None, "trk", "car", "Ada", 90000, environment="rig")
    payload2, changed = fold_lap(payload, "trk", "car", "Ada", 90000, environment="rig")
    assert changed is False


def test_fold_lap_different_drivers_independent():
    payload, _ = fold_lap(None, "trk", "car", "Ada", 90000, environment="rig")
    payload, _ = fold_lap(payload, "trk", "car", "Bo", 88000, environment="rig")
    assert payload["trk"]["car"]["Ada"] == 90000
    assert payload["trk"]["car"]["Bo"] == 88000


# ---------------------------------------------------------------------------
# to_rows returns exactly one row per driver/track/car combo
# ---------------------------------------------------------------------------


def test_to_rows_one_row_per_combo():
    payload, _ = fold_lap(None, "nurburgring", "bmw_1m", "Ada", 91234, environment="rig")
    payload, _ = fold_lap(payload, "nurburgring", "bmw_1m", "Bo", 90000, environment="rig")
    payload, _ = fold_lap(payload, "spa", "gt3", "Ada", 105000, environment="rig")

    rows = to_rows("exp1", payload)
    assert len(rows) == 3
    keys = {(r["track"], r["carModel"], r["driver"]) for r in rows}
    assert keys == {
        ("nurburgring", "bmw_1m", "Ada"),
        ("nurburgring", "bmw_1m", "Bo"),
        ("spa", "gt3", "Ada"),
    }


def test_to_rows_empty_payload_returns_empty():
    assert to_rows("exp1", None) == []
    assert to_rows("exp1", {}) == []
