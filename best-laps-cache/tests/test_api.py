"""Smoke tests for the /best-laps GET wrapper over the materialized view."""

from __future__ import annotations

import csv
import io
import os

os.environ.setdefault("LAKE_TABLE", "ac_telemetry_prod")

from fastapi.testclient import TestClient  # noqa: E402

from best_laps_cache.api import build_best_laps_table, create_app  # noqa: E402
from best_laps_cache.materialized import MaterializedView  # noqa: E402
from best_laps_cache.settings import get_settings  # noqa: E402
from best_laps_cache.state_model import fold_lap, to_rows  # noqa: E402

EXP = "baseline"


def _view() -> MaterializedView:
    """A materialized view holding one experiment's built rows."""
    payload, _ = fold_lap(
        None, "ks_nurburgring", "bmw_1m", "Ludvík", 91234, environment="prod-rig"
    )
    payload, _ = fold_lap(
        payload, "ks_nurburgring", "bmw_1m", "Ada", 90000, environment="prod-rig"
    )
    view = MaterializedView()
    view.put(EXP, to_rows(EXP, payload), environment="prod-rig")
    return view


def _client() -> TestClient:
    return TestClient(create_app(_view(), get_settings()))


def test_csv_shape_matches_lake_query():
    resp = _client().get("/best-laps")
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
    # Sorted fastest-first within group.
    assert rows[0]["driver"] == "Ada"
    assert rows[0]["iBestTime"] == "90000"


def test_filter_by_track_and_car():
    rows, _ = build_best_laps_table(
        _view(), track="ks_nurburgring", car_model="bmw_1m"
    )
    assert len(rows) == 2
    rows, _ = build_best_laps_table(_view(), track="nope")
    assert rows == []


def test_filter_by_driver_backcompat():
    resp = _client().get("/best-laps", params={"driver": "Ludvík"})
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["driver"] == "Ludvík"


def test_json_envelope():
    resp = _client().get("/best-laps", params={"format": "json"})
    body = resp.json()
    assert body["table"] == "ac_telemetry_prod"
    assert body["columns"][-1] == "iBestTime"
    assert body["row_count"] == 2
    assert body["source"] == "best-laps-cache"


def test_healthz():
    resp = _client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["active_experiment"] == EXP
