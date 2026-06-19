"""Smoke tests for the /best-laps on-demand State read round-trip.

No live broker: a fake Pipeline stand-in replaces the produce + SDF read with a
direct, in-process correlation (and the State payload to "read"). This exercises
the req_id correlation, timeout path, and CSV/JSON wire-compat.
"""

from __future__ import annotations

import csv
import io
import os

os.environ.setdefault("LAKE_TABLE", "ac_telemetry_prod")

from fastapi.testclient import TestClient  # noqa: E402

from best_laps_cache.api import build_best_laps_table, create_app  # noqa: E402
from best_laps_cache.request_bridge import PendingRequests  # noqa: E402
from best_laps_cache.settings import get_settings  # noqa: E402
from best_laps_cache.state_model import fold_lap  # noqa: E402

EXP = "baseline"


def _payload() -> dict:
    """A nested State payload as the SDF would read it in-context."""
    payload, _ = fold_lap(
        None, "ks_nurburgring", "bmw_1m", "Ludvík", 91234, environment="prod-rig"
    )
    payload, _ = fold_lap(
        payload, "ks_nurburgring", "bmw_1m", "Ada", 90000, environment="prod-rig"
    )
    return payload


class _FakePipeline:
    """Stand-in for Pipeline.

    `produce_get_request` synchronously delivers the configured payload to the
    pending slot — simulating the SDF reading State in-context — unless
    `simulate_timeout` is set (then it never delivers).
    """

    def __init__(
        self,
        pending: PendingRequests,
        *,
        payload: dict | None,
        active: str = EXP,
        simulate_timeout: bool = False,
    ) -> None:
        self._pending = pending
        self._payload = payload
        self._active = active
        self._simulate_timeout = simulate_timeout
        self.produced: list[tuple[str, str]] = []

    def active_experiment(self) -> str:
        return self._active

    def produce_get_request(self, experiment: str, req_id: str) -> None:
        self.produced.append((experiment, req_id))
        if not self._simulate_timeout:
            self._pending.deliver(req_id, self._payload)


def _client(pipeline: _FakePipeline, pending: PendingRequests) -> TestClient:
    return TestClient(create_app(pipeline, pending, get_settings()))


# -- build_best_laps_table (transient payload core) -----------------------


def test_build_table_from_transient_payload():
    rows = build_best_laps_table(EXP, _payload())
    assert len(rows) == 2
    assert rows[0]["driver"] == "Ada"  # fastest first within group
    assert rows[0]["iBestTime"] == 90000


def test_build_table_filter_track_and_car():
    rows = build_best_laps_table(
        EXP, _payload(), track="ks_nurburgring", car_model="bmw_1m"
    )
    assert len(rows) == 2
    assert build_best_laps_table(EXP, _payload(), track="nope") == []


def test_build_table_empty_payload():
    assert build_best_laps_table(EXP, None) == []


# -- round-trip via the API -----------------------------------------------


def test_csv_shape_matches_lake_query():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, payload=_payload())
    resp = _client(pipeline, pending).get("/best-laps")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    assert reader.fieldnames == [
        "environment",
        "experiment",
        "track",
        "carModel",
        "driver",
        "iBestTime",
    ]
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["driver"] == "Ada"
    assert rows[0]["iBestTime"] == "90000"
    # The handler produced exactly one get_request keyed by the active experiment.
    assert pipeline.produced and pipeline.produced[0][0] == EXP
    # No payload retained: the slot was consumed + deleted.
    assert pending.pending_count() == 0


def test_filter_by_driver_backcompat():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, payload=_payload())
    resp = _client(pipeline, pending).get("/best-laps", params={"driver": "Ludvík"})
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["driver"] == "Ludvík"


def test_json_envelope():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, payload=_payload())
    resp = _client(pipeline, pending).get("/best-laps", params={"format": "json"})
    body = resp.json()
    assert body["table"] == "ac_telemetry_prod"
    assert body["columns"][-1] == "iBestTime"
    assert body["row_count"] == 2
    assert body["source"] == "best-laps-cache"


def test_timeout_returns_empty_board_200():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, payload=_payload(), simulate_timeout=True)
    # Patch the module-level timeout so the test does not block for 3s.
    import best_laps_cache.api as api_mod

    orig = api_mod._READ_TIMEOUT_S
    api_mod._READ_TIMEOUT_S = 0.05
    try:
        resp = _client(pipeline, pending).get("/best-laps")
    finally:
        api_mod._READ_TIMEOUT_S = orig
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert rows == []
    # Timed-out slot must be cleaned up.
    assert pending.pending_count() == 0


def test_no_active_experiment_empty_board():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, payload=_payload(), active="")
    resp = _client(pipeline, pending).get("/best-laps")
    assert resp.status_code == 200
    assert list(csv.DictReader(io.StringIO(resp.text))) == []
    # No experiment -> no round-trip produced.
    assert pipeline.produced == []


def test_healthz():
    pending = PendingRequests()
    pipeline = _FakePipeline(pending, payload=_payload())
    resp = _client(pipeline, pending).get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["active_experiment"] == EXP
    assert body["in_flight_requests"] == 0
    assert "materialized_experiments" not in body
