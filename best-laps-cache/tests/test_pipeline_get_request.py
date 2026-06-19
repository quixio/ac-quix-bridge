"""Unit test the SDF get_request read branch without a broker.

Pipeline._handle_event is invoked directly with a fake State, bypassing the
Application/broker (constructed via __new__). This verifies the get_request
branch reads State for the keyed experiment and delivers it to the bridge by
req_id, and that write-only event types still fold into State.
"""

from __future__ import annotations

import os

os.environ.setdefault("LAKE_TABLE", "ac_telemetry_prod")

from best_laps_cache.pipeline import Pipeline  # noqa: E402
from best_laps_cache.request_bridge import PendingRequests  # noqa: E402
from best_laps_cache.settings import get_settings  # noqa: E402


class _FakeState:
    def __init__(self, store: dict | None = None) -> None:
        self._store = dict(store or {})

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value) -> None:
        self._store[key] = value


def _pipeline(pending: PendingRequests) -> Pipeline:
    p = Pipeline.__new__(Pipeline)
    p._settings = get_settings()
    p._pending = pending
    return p


def test_get_request_reads_state_and_delivers():
    pending = PendingRequests()
    payload = {"_env": "rig", "trk": {"car": {"drv": 90000}}}
    state = _FakeState({"baseline": payload})
    pipeline = _pipeline(pending)

    req_id = pending.open()
    pipeline._handle_event(
        {"type": "get_request", "experiment": "baseline", "req_id": req_id}, state
    )
    delivered, got = pending.wait(req_id, timeout=1.0)
    assert delivered is True
    assert got == payload


def test_get_request_empty_state_delivers_none():
    pending = PendingRequests()
    pipeline = _pipeline(pending)
    req_id = pending.open()
    pipeline._handle_event(
        {"type": "get_request", "experiment": "cold", "req_id": req_id},
        _FakeState(),
    )
    delivered, got = pending.wait(req_id, timeout=1.0)
    assert delivered is True
    assert got is None


def test_lap_event_folds_into_state_only():
    pending = PendingRequests()
    pipeline = _pipeline(pending)
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
    # State written; nothing delivered/pending (write-only path).
    assert state.get("baseline")["trk"]["car"]["drv"] == 90000
    assert pending.pending_count() == 0
