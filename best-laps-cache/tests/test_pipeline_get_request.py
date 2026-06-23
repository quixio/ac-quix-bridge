"""Unit tests for Pipeline._handle_event — State fold + mirror update.

Pipeline._handle_event is invoked directly with a fake State and a real
BestLapsMirror, bypassing the Application/broker (constructed via __new__).
Verifies that lap/seed/read events fold into State and update the mirror,
and that unknown event types are ignored.
"""

from __future__ import annotations

import os

os.environ.setdefault("LAKE_TABLE", "ac_telemetry_prod")

from best_laps_cache.mirror import BestLapsMirror  # noqa: E402
from best_laps_cache.pipeline import Pipeline  # noqa: E402
from best_laps_cache.settings import get_settings  # noqa: E402


class _FakeState:
    def __init__(self, store: dict | None = None) -> None:
        self._store = dict(store or {})

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value) -> None:
        self._store[key] = value


def _pipeline(mirror: BestLapsMirror) -> Pipeline:
    p = Pipeline.__new__(Pipeline)
    p._settings = get_settings()
    p._mirror = mirror
    return p


def test_lap_event_folds_into_state_and_updates_mirror():
    mirror = BestLapsMirror()
    pipeline = _pipeline(mirror)
    state = _FakeState()

    pipeline._handle_event(
        {
            "type": "lap",
            "experiment": "baseline",
            "environment": "rig",
            "track": "trk",
            "carModel": "car",
            "driver": "drv",
            "best_ms": 90000,
        },
        state,
    )

    assert state.get("baseline")["trk"]["car"]["drv"] == 90000
    assert mirror.get("baseline")["trk"]["car"]["drv"] == 90000


def test_lap_event_worse_time_no_state_or_mirror_update():
    mirror = BestLapsMirror()
    pipeline = _pipeline(mirror)
    initial = {"_env": "rig", "trk": {"car": {"drv": 80000}}}
    state = _FakeState({"baseline": initial})
    mirror.update("baseline", initial)

    pipeline._handle_event(
        {
            "type": "lap",
            "experiment": "baseline",
            "environment": "rig",
            "track": "trk",
            "carModel": "car",
            "driver": "drv",
            "best_ms": 95000,  # worse than 80000
        },
        state,
    )

    # State and mirror unchanged
    assert state.get("baseline")["trk"]["car"]["drv"] == 80000
    assert mirror.get("baseline")["trk"]["car"]["drv"] == 80000


def test_seed_event_folds_when_state_empty():
    mirror = BestLapsMirror()
    pipeline = _pipeline(mirror)
    state = _FakeState()

    pipeline._handle_event(
        {
            "type": "seed",
            "experiment": "expA",
            "environment": "env",
            "rows": [
                {"track": "trk", "carModel": "car", "driver": "Ada", "best_lap_ms": 82345},
            ],
        },
        state,
    )

    assert state.get("expA")["trk"]["car"]["Ada"] == 82345
    assert mirror.get("expA")["trk"]["car"]["Ada"] == 82345


def test_seed_event_skips_when_state_populated():
    mirror = BestLapsMirror()
    pipeline = _pipeline(mirror)
    existing = {"_env": "env", "trk": {"car": {"Ada": 80000}}}
    state = _FakeState({"expA": dict(existing)})

    pipeline._handle_event(
        {
            "type": "seed",
            "experiment": "expA",
            "environment": "env",
            "rows": [
                {"track": "trk", "carModel": "car", "driver": "Ada", "best_lap_ms": 82345},
            ],
        },
        state,
    )

    # Populated state is not clobbered
    assert state.get("expA") == existing


def test_unknown_event_type_is_ignored():
    mirror = BestLapsMirror()
    pipeline = _pipeline(mirror)
    state = _FakeState()

    # Should not raise, should not touch state or mirror
    pipeline._handle_event(
        {"type": "get_request", "experiment": "baseline", "req_id": "x"},
        state,
    )
    assert state.get("baseline") is None
    assert mirror.get("baseline") is None


def test_empty_experiment_ignored():
    mirror = BestLapsMirror()
    pipeline = _pipeline(mirror)
    state = _FakeState()

    pipeline._handle_event({"type": "lap", "experiment": "", "best_ms": 90000}, state)

    assert mirror.experiments() == []
