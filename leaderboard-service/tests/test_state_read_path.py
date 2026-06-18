"""Unit tests for the per-request in-context read round-trip (mock produce, no broker).

Verifies the ``req_id`` correlation + timeout mechanism end-to-end against a fake
pipeline whose ``produce_get_request`` simulates the SDF reading State in-context
and delivering the transient payload back through the real ``PendingRequests``
bridge — plus the timeout path (produce that never delivers).
"""

from __future__ import annotations

import threading

from leaderboard_service_state import read_path
from leaderboard_service_state.request_bridge import PendingRequests
from leaderboard_service_state.state_model import fold_best_lap

_GV = [1000, 2000, 3000]


class _FakePipeline:
    """Simulates the SDF: on produce_get_request, deliver *payload* via the bridge."""

    def __init__(self, pending: PendingRequests, payload, *, deliver: bool = True):
        self._pending = pending
        self._payload = payload
        self._deliver = deliver

    def produce_get_request(self, experiment: str, req_id: str) -> None:
        if not self._deliver:
            return  # simulate a broker round-trip that never comes back
        # Deliver from another thread to exercise the cross-thread wakeup.
        threading.Thread(
            target=self._pending.deliver,
            args=(req_id, self._payload),
            daemon=True,
        ).start()


def _payload():
    payload, _ = fold_best_lap(
        None, "spa", "bmw_1m", "ada", 90000, _GV, 7, environment="rig"
    )
    payload, _ = fold_best_lap(
        payload, "spa", "bmw_1m", "bo", 92000, [1, 2, 4], 3, environment="rig"
    )
    return payload


def test_read_round_trip_delivers_payload():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, _payload())
    payload, delivered = read_path.read_experiment_payload(
        pipeline, pending, "EXP-1", timeout=1.0
    )
    assert delivered is True
    assert payload["spa"]["bmw_1m"]["ada"]["best_lap_ms"] == 90000
    # Slot cleaned up — nothing persists between requests.
    assert pending.pending_count() == 0


def test_read_round_trip_timeout_returns_empty_and_cleans_up():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, _payload(), deliver=False)
    payload, delivered = read_path.read_experiment_payload(
        pipeline, pending, "EXP-1", timeout=0.05
    )
    assert delivered is False
    assert payload is None
    assert pending.pending_count() == 0


def test_historicals_from_payload_builds_entries():
    class _Entry:
        def __init__(self, best_lap_ms, best_lap_number, gate_vector):
            self.best_lap_ms = best_lap_ms
            self.best_lap_number = best_lap_number
            self.gate_vector = gate_vector

    hist = read_path.historicals_from_payload("EXP-1", _payload(), _Entry)
    group = hist[("spa", "bmw_1m", "EXP-1")]
    assert group["ada"].best_lap_ms == 90000
    assert group["ada"].gate_vector == _GV
    assert group["bo"].best_lap_number == 3


def test_best_laps_from_payload_keys_by_full_tuple():
    best = read_path.best_laps_from_payload("EXP-1", _payload())
    key = ("spa", "bmw_1m", "EXP-1", "rig")
    assert best[key] == {"ada": 90000, "bo": 92000}


def test_empty_payload_yields_empty_views():
    assert read_path.historicals_from_payload("EXP-1", None, object) == {}
    assert read_path.best_laps_from_payload("EXP-1", None) == {}
