"""Unit tests for reconcile SQL shape + row reduction."""

from __future__ import annotations

from best_laps_cache.reconcile import build_reconcile_sql, reduce_rows
from best_laps_cache.store import make_key


def test_reconcile_sql_has_no_group_by_or_cte():
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime").upper()
    assert "GROUP BY" not in sql
    assert "MIN(" not in sql
    assert "WITH " not in sql
    assert "WHERE IBESTTIME > 0" in sql


def test_reduce_rows_per_group_min_and_drops():
    rows = [
        {
            "environment": "env",
            "experiment": "exp",
            "track": "trk",
            "carModel": "car",
            "driver": "Ada",
            "iBestTime": 83110,
        },
        {
            "environment": "env",
            "experiment": "exp",
            "track": "trk",
            "carModel": "car",
            "driver": "Ada",
            "iBestTime": 82345,  # faster, wins
        },
        {
            "environment": "env",
            "experiment": "exp",
            "track": "trk",
            "carModel": "car",
            "driver": "Bo",
            "iBestTime": 91000,
        },
        {  # non-positive -> dropped
            "environment": "env",
            "experiment": "exp",
            "track": "trk",
            "carModel": "car",
            "driver": "Bo",
            "iBestTime": 0,
        },
        {  # blank driver -> dropped
            "environment": "env",
            "experiment": "exp",
            "track": "trk",
            "carModel": "car",
            "driver": "",
            "iBestTime": 70000,
        },
    ]
    out = reduce_rows(rows, "iBestTime")
    assert out == {
        make_key("env", "exp", "trk", "car", "Ada"): 82345,
        make_key("env", "exp", "trk", "car", "Bo"): 91000,
    }
