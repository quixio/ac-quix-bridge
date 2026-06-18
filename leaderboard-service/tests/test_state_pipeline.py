"""Unit tests for the stateful event handler fold/min + seed gate (no broker).

``app.topic()`` hits the broker, so we bypass ``Pipeline.__init__``/``_build`` via
``__new__`` and inject just the settings + a real PendingRequests bridge. The
``lap``/``seed``/``get_request`` branches of ``_handle_event`` touch only the faked
State + the bridge, so this is sufficient and broker-free.
"""

from __future__ import annotations

from typing import Any

from leaderboard_service_state.pipeline import Pipeline
from leaderboard_service_state.request_bridge import PendingRequests
from leaderboard_service_state.settings import Settings

_GV = [1000, 2000, 3000]


def _settings() -> Settings:
    return Settings(
        sdk_token=None,
        broker_address="localhost:9092",
        consumer_group="t",
        raw_topic="raw",
        session_topic="session",
        config_topic="config",
        events_topic="leaderboard-events",
        config_api_url=None,
        dcm_timeout_s=5.0,
        lakehouse_query_url=None,
        lakehouse_query_token=None,
        lake_table="ac_telemetry",
        col_best_time="iBestTime",
        col_current_time="iCurrentTime",
        col_normalized_position="normalizedCarPosition",
        gate_count=3,
        state_dir="state",
        max_lap_samples=20000,
    )


class _FakeState:
    """Minimal in-memory stand-in for QuixStreams' State (no RocksDB)."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._d: dict[str, Any] = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value


def _pipeline():
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._settings = _settings()
    pipeline._gate_count = 3
    pipeline._pending = PendingRequests()
    return pipeline


def _lap_event(driver, lap_ms, gv=_GV, lap_number=4):
    return {
        "type": "lap",
        "experiment": "EXP-1",
        "environment": "rig",
        "track": "spa",
        "carModel": "bmw_1m",
        "driver": driver,
        "lap_ms": lap_ms,
        "lap_number": lap_number,
        "gate_vector": gv,
    }


def test_lap_event_folds_and_min_updates():
    pipeline = _pipeline()
    state = _FakeState()
    pipeline._handle_event(_lap_event("ada", 91000), state)
    rec = state.get("EXP-1")["spa"]["bmw_1m"]["ada"]
    assert rec["best_lap_ms"] == 91000

    # Slower lap: no change.
    pipeline._handle_event(_lap_event("ada", 92000, gv=[9, 9, 9]), state)
    assert state.get("EXP-1")["spa"]["bmw_1m"]["ada"]["best_lap_ms"] == 91000

    # Faster lap: whole record replaced.
    pipeline._handle_event(_lap_event("ada", 90000, gv=[8, 18, 28], lap_number=12), state)
    rec = state.get("EXP-1")["spa"]["bmw_1m"]["ada"]
    assert rec == {"best_lap_ms": 90000, "best_lap_number": 12, "gate_vector": [8, 18, 28]}


def test_seed_handler_folds_when_state_empty():
    pipeline = _pipeline()
    state = _FakeState()
    msg = {
        "type": "seed",
        "experiment": "EXP-1",
        "environment": "rig",
        "rows": [
            {"track": "spa", "carModel": "bmw_1m", "driver": "ada",
             "best_lap_ms": 90000, "best_lap_number": 3, "gate_vector": _GV},
        ],
    }
    pipeline._handle_event(msg, state)
    assert state.get("EXP-1")["spa"]["bmw_1m"]["ada"]["best_lap_ms"] == 90000


def test_seed_handler_skips_when_state_populated():
    pipeline = _pipeline()
    existing = {"_env": "rig", "spa": {"bmw_1m": {"ada": {"best_lap_ms": 80000, "best_lap_number": 1, "gate_vector": _GV}}}}
    state = _FakeState({"EXP-1": dict(existing)})
    msg = {
        "type": "seed",
        "experiment": "EXP-1",
        "environment": "rig",
        "rows": [
            {"track": "spa", "carModel": "bmw_1m", "driver": "ada",
             "best_lap_ms": 70000, "best_lap_number": 9, "gate_vector": _GV},
        ],
    }
    pipeline._handle_event(msg, state)
    # No clobber: populated experiment left exactly as-is.
    assert state.get("EXP-1") == existing


def test_get_request_delivers_payload_via_bridge():
    pipeline = _pipeline()
    pending = pipeline._pending
    state = _FakeState({"EXP-1": {"_env": "rig", "spa": {"bmw_1m": {"ada": {"best_lap_ms": 90000, "best_lap_number": 1, "gate_vector": _GV}}}}})
    req_id = pending.open()
    pipeline._handle_event(
        {"type": "get_request", "experiment": "EXP-1", "req_id": req_id}, state
    )
    delivered, payload = pending.wait(req_id, timeout=1.0)
    assert delivered is True
    assert payload["spa"]["bmw_1m"]["ada"]["best_lap_ms"] == 90000
