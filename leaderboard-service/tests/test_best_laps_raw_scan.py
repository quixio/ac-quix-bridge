"""`_query_best_laps_min` must avoid the timing-out server-side aggregation.

On the slow byox lake the `SELECT driver, MIN(iBestTime) ... GROUP BY driver`
SQL hits the 30s client timeout. That timeout propagated out of
`_query_best_laps_min` to *every* caller — both the `/best-laps` route and the
TTL-refresh `_run_one` tick in `live_telemetry` — leaving the best-laps cache
empty (`0 groups cached`). The fix makes the raw scan
(`SELECT driver, iBestTime ... WHERE ... AND iBestTime > 0`, no `GROUP BY`)
the primary path, reduced to per-driver minima in Python.

These tests stub `LakehouseClient.query` (no lake / no network) and assert:

1. With the default settings (`lake_server_aggregation=False`) the function
   returns per-driver minima from a raw-scan response and never issues a
   `MIN(...) GROUP BY` query.
2. With the opt-in fast-path enabled, a `MIN` timeout transparently falls back
   to the raw scan *inside the function*, so no caller sees the bare timeout.
"""

from __future__ import annotations

import os

import httpx
import pandas as pd
import pytest

os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-token")
os.environ.setdefault("CONFIG_API_URL", "http://localhost:8001")

from api import settings as settings_mod  # noqa: E402
from api.routes import leaderboard_real  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings` is `lru_cache`d; clear it so env overrides take."""
    settings_mod.get_settings.cache_clear()
    yield
    settings_mod.get_settings.cache_clear()


def _raw_scan_df() -> pd.DataFrame:
    """A stubbed raw-scan response: raw (driver, iBestTime) rows.

    Two laps for "Ada" (min 82345), one for "Bo", and a non-positive /
    blank-driver row that must be dropped.
    """
    col = settings_mod.get_settings().col_best_time
    return pd.DataFrame(
        [
            {"driver": "Ada", col: 83110},
            {"driver": "Ada", col: 82345},
            {"driver": "Bo", col: 91000},
            {"driver": "Bo", col: 0},  # non-positive -> ignored
            {"driver": "", col: 70000},  # blank driver -> ignored
        ]
    )


def test_raw_scan_primary_returns_minima_without_group_by(monkeypatch):
    """Default path: per-driver MIN from the scan, no `MIN(...) GROUP BY`."""
    issued: list[str] = []

    def fake_query(self, sql: str) -> pd.DataFrame:
        issued.append(sql)
        return _raw_scan_df()

    monkeypatch.setattr(leaderboard_real.LakehouseClient, "query", fake_query)

    result = leaderboard_real._query_best_laps_min(
        "http://lake",
        "tok",
        track="ks_nurburgring",
        car="ferrari_488",
        experiment="exp1",
        environment="env1",
    )

    assert result == {"Ada": 82345, "Bo": 91000}
    assert len(issued) == 1
    sql = issued[0].upper()
    assert "MIN(" not in sql
    assert "GROUP BY" not in sql


def test_min_timeout_falls_back_to_raw_scan(monkeypatch):
    """Opt-in fast-path: a MIN timeout falls back to the scan in-function."""
    monkeypatch.setenv("LAKE_SERVER_AGGREGATION", "true")
    settings_mod.get_settings.cache_clear()

    issued: list[str] = []

    def fake_query(self, sql: str) -> pd.DataFrame:
        issued.append(sql)
        if "MIN(" in sql.upper():
            raise httpx.ReadTimeout("30s client timeout", request=None)
        return _raw_scan_df()

    monkeypatch.setattr(leaderboard_real.LakehouseClient, "query", fake_query)

    result = leaderboard_real._query_best_laps_min(
        "http://lake",
        "tok",
        track="ks_nurburgring",
        car="ferrari_488",
        experiment="exp1",
        environment="env1",
    )

    # Fast-path MIN was attempted, timed out, and the raw scan recovered.
    assert result == {"Ada": 82345, "Bo": 91000}
    assert len(issued) == 2
    assert "MIN(" in issued[0].upper()
    assert "GROUP BY" not in issued[1].upper()
