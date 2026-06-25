"""Unit tests for the cold-start seed helpers (SQL shape + row reduction)."""

from __future__ import annotations

from best_laps_cache.seed import build_reconcile_sql, reduce_rows


def test_seed_sql_aggregates_to_one_row_per_group():
    """build_reconcile_sql must use MIN/GROUP BY to avoid full-table OOM scans."""
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime").upper()
    # Aggregated path: server-side MIN eliminates duplicate 50 Hz ticks.
    assert "MIN(IBESTTIME)" in sql
    assert "GROUP BY" in sql
    assert "WHERE IBESTTIME > 0" in sql
    # No CTEs (unsupported on the BYOX lakehouse query endpoint).
    assert "WITH " not in sql


def test_seed_sql_valid_laps_false_aggregates_ilasttime():
    """valid_laps_only=False must aggregate iLastTime, not iBestTime."""
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime", valid_laps_only=False).upper()
    assert "MIN(ILASTTIME)" in sql
    assert "GROUP BY" in sql
    assert "WHERE ILASTTIME > 0" in sql


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
