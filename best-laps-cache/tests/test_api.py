"""Smoke tests for GET /best-laps — direct in-memory mirror read.

No live broker: a fake Pipeline stand-in plus a real BestLapsMirror replace the
old round-trip. This exercises CSV/JSON wire-compat, driver filter, empty-board
paths, and the healthz endpoint.
"""

from __future__ import annotations

import csv
import io
import os

os.environ.setdefault("LAKE_TABLE", "ac_telemetry_prod")

from fastapi.testclient import TestClient  # noqa: E402

from best_laps_cache.api import build_best_laps_table, create_app  # noqa: E402
from best_laps_cache.mirror import BestLapsMirror  # noqa: E402
from best_laps_cache.settings import get_settings  # noqa: E402
from best_laps_cache.state_model import fold_lap  # noqa: E402

EXP = "baseline"


def _payload() -> dict:
    """A nested State payload as the SDF would write to the mirror."""
    payload, _ = fold_lap(
        None, "ks_nurburgring", "bmw_1m", "Ludvík", 91234, environment="prod-rig"
    )
    payload, _ = fold_lap(
        payload, "ks_nurburgring", "bmw_1m", "Ada", 90000, environment="prod-rig"
    )
    return payload


class _FakePipeline:
    """Stand-in for Pipeline — just exposes active_experiment()."""

    def __init__(self, active: str = EXP) -> None:
        self._active = active

    def active_experiment(self) -> str:
        return self._active


def _client(pipeline: _FakePipeline, mirror: BestLapsMirror) -> TestClient:
    return TestClient(create_app(pipeline, mirror, get_settings()))


# -- build_best_laps_table (payload core) ---------------------------------


def test_build_table_from_payload():
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


# -- GET /best-laps via mirror --------------------------------------------


def test_csv_shape_matches_lake_query():
    mirror = BestLapsMirror()
    mirror.update(EXP, _payload())
    pipeline = _FakePipeline()
    resp = _client(pipeline, mirror).get("/best-laps")
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


def test_csv_empty_when_mirror_not_yet_warm():
    """Mirror has no entry yet → empty board, not an error."""
    mirror = BestLapsMirror()  # nothing in it
    pipeline = _FakePipeline()
    resp = _client(pipeline, mirror).get("/best-laps")
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert rows == []


def test_filter_by_driver_backcompat():
    mirror = BestLapsMirror()
    mirror.update(EXP, _payload())
    pipeline = _FakePipeline()
    resp = _client(pipeline, mirror).get("/best-laps", params={"driver": "Ludvík"})
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["driver"] == "Ludvík"


def test_json_envelope():
    mirror = BestLapsMirror()
    mirror.update(EXP, _payload())
    pipeline = _FakePipeline()
    resp = _client(pipeline, mirror).get("/best-laps", params={"format": "json"})
    body = resp.json()
    assert body["table"] == "ac_telemetry_prod"
    assert body["columns"][-1] == "iBestTime"
    assert body["row_count"] == 2
    assert body["source"] == "best-laps-cache"


def test_no_active_experiment_empty_board():
    mirror = BestLapsMirror()
    pipeline = _FakePipeline(active="")
    resp = _client(pipeline, mirror).get("/best-laps")
    assert resp.status_code == 200
    assert list(csv.DictReader(io.StringIO(resp.text))) == []


def test_healthz():
    mirror = BestLapsMirror()
    mirror.update(EXP, _payload())
    pipeline = _FakePipeline()
    resp = _client(pipeline, mirror).get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["active_experiment"] == EXP
    assert body["materialized_experiments"] == [EXP]
