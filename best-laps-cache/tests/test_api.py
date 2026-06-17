"""Smoke tests for the /best-laps CSV + JSON endpoint shape."""

from __future__ import annotations

import csv
import io
import os

os.environ.setdefault("LAKE_TABLE", "ac_telemetry_prod")

from fastapi.testclient import TestClient  # noqa: E402

from best_laps_cache.api import create_app  # noqa: E402
from best_laps_cache.settings import get_settings  # noqa: E402
from best_laps_cache.store import BestLapsStore  # noqa: E402


def _client() -> tuple[TestClient, BestLapsStore]:
    store = BestLapsStore()
    store.update_live("prod-rig", "baseline", "ks_nurburgring", "bmw_1m", "Ludvík", 91234)
    store.update_live("prod-rig", "baseline", "ks_nurburgring", "bmw_1m", "Ada", 90000)
    app = create_app(store, get_settings())
    return TestClient(app), store


def test_csv_shape_matches_lake_query():
    client, _ = _client()
    resp = client.get("/best-laps")
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


def test_filter_by_driver():
    client, _ = _client()
    resp = client.get("/best-laps", params={"driver": "Ludvík"})
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert len(rows) == 1
    assert rows[0]["driver"] == "Ludvík"


def test_json_envelope():
    client, _ = _client()
    resp = client.get("/best-laps", params={"format": "json"})
    body = resp.json()
    assert body["table"] == "ac_telemetry_prod"
    assert body["columns"][-1] == "iBestTime"
    assert body["row_count"] == 2
    assert body["source"] == "best-laps-cache"


def test_healthz():
    client, _ = _client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["cached_keys"] == 2
