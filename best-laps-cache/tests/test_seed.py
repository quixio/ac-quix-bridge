"""Unit tests for the cold-start seed helpers (SQL shape + row reduction)."""

from __future__ import annotations

from best_laps_cache.seed import build_reconcile_sql, reduce_rows


def test_seed_sql_is_byox_safe():
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
        ("env", "exp", "trk", "car", "Ada"): 82345,
        ("env", "exp", "trk", "car", "Bo"): 91000,
    }
