"""Unit tests for the VALID_LAPS_ONLY flag.

Covers:
1. Boot-seed SQL shape when valid_laps_only=True (iBestTime, no iLastTime)
2. Boot-seed SQL shape when valid_laps_only=False (MIN(iLastTime) AS iBestTime, GROUP BY)
3. _enrich_raw reads iBestTime when valid_laps_only=True
4. _enrich_raw reads iLastTime when valid_laps_only=False (captures invalid laps)
5. _enrich_raw sentinel values are passed through (filtered by downstream guard)
6. _is_new_best stateful min-comparison is flag-agnostic
"""

from __future__ import annotations

from best_laps_cache.pipeline import Pipeline, _LAST_BEST_KEY
from best_laps_cache.seed import build_reconcile_sql
from best_laps_cache.settings import Settings
from best_laps_cache.state_model import INT_MAX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(valid_laps_only: bool) -> Settings:
    return Settings(
        sdk_token=None,
        broker_address=None,
        consumer_group="test",
        raw_topic="raw",
        session_topic="session",
        config_topic="config",
        config_api_url=None,
        dcm_timeout_s=5.0,
        lakehouse_query_url=None,
        lakehouse_query_token=None,
        lake_table="ac_telemetry_prod",
        col_best_time="iBestTime",
        http_host="0.0.0.0",
        http_port=80,
        state_dir="state",
        boot_seed_gate_timeout_s=60.0,
        valid_laps_only=valid_laps_only,
    )


class _FakeEnrichment:
    """Returns fixed enrichment fields regardless of the input payload."""

    def enrich(self, value: dict) -> dict:
        return {
            "environment": "env",
            "experiment": "exp",
            "track": "trk",
            "carModel": "car",
            "driver": "drv",
        }


class _FakeState:
    def __init__(self, store: dict | None = None) -> None:
        self._store = dict(store or {})

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value) -> None:
        self._store[key] = value


def _pipeline(valid_laps_only: bool) -> Pipeline:
    p = Pipeline.__new__(Pipeline)
    p._settings = _make_settings(valid_laps_only)
    p._enrichment = _FakeEnrichment()
    return p


# ---------------------------------------------------------------------------
# 1. Boot-seed SQL — valid laps only (existing behaviour)
# ---------------------------------------------------------------------------


def test_boot_seed_sql_valid_only_contains_ibest_time():
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime", valid_laps_only=True)
    assert "iBestTime" in sql
    assert "iLastTime" not in sql


def test_boot_seed_sql_valid_only_no_group_by():
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime", valid_laps_only=True).upper()
    assert "GROUP BY" not in sql
    assert "MIN(" not in sql


# ---------------------------------------------------------------------------
# 2. Boot-seed SQL — all finished laps (iLastTime with GROUP BY)
# ---------------------------------------------------------------------------


def test_boot_seed_sql_all_laps_uses_min_ilasttime():
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime", valid_laps_only=False)
    assert "MIN(iLastTime)" in sql
    assert "AS iBestTime" in sql


def test_boot_seed_sql_all_laps_has_group_by():
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime", valid_laps_only=False).upper()
    assert "GROUP BY" in sql


def test_boot_seed_sql_all_laps_no_ibest_in_where():
    # The WHERE clause should filter on iLastTime, not iBestTime
    sql = build_reconcile_sql("ac_telemetry_prod", "iBestTime", valid_laps_only=False)
    where_clause = sql.split("WHERE", 1)[1]
    assert "iLastTime" in where_clause
    assert "iBestTime" not in where_clause


# ---------------------------------------------------------------------------
# 3. _enrich_raw with valid_laps_only=True reads iBestTime
# ---------------------------------------------------------------------------


def test_enrich_raw_valid_only_reads_ibest_time():
    p = _pipeline(valid_laps_only=True)
    result = p._enrich_raw({"iBestTime": 92000, "iLastTime": INT_MAX - 1})
    assert result["best_ms"] == 92000


def test_enrich_raw_valid_only_ignores_ilast_time():
    # iBestTime is the sentinel (0) but iLastTime is fine; valid_laps_only=True
    # should still report best_ms=0 (iBestTime wins).
    p = _pipeline(valid_laps_only=True)
    result = p._enrich_raw({"iBestTime": 0, "iLastTime": 85000})
    assert result["best_ms"] == 0


# ---------------------------------------------------------------------------
# 4. _enrich_raw with valid_laps_only=False reads iLastTime (invalid laps)
# ---------------------------------------------------------------------------


def test_enrich_raw_all_laps_reads_ilast_time():
    p = _pipeline(valid_laps_only=False)
    # iBestTime is INT_MAX (lap was invalid), iLastTime carries the real time.
    result = p._enrich_raw({"iBestTime": INT_MAX, "iLastTime": 92000})
    assert result["best_ms"] == 92000


def test_enrich_raw_all_laps_ignores_ibest_time():
    # Both fields set; with valid_laps_only=False we must pick iLastTime.
    p = _pipeline(valid_laps_only=False)
    result = p._enrich_raw({"iBestTime": 80000, "iLastTime": 92000})
    assert result["best_ms"] == 92000


# ---------------------------------------------------------------------------
# 5. Sentinel values — _enrich_raw passes them through unchanged
#    (the existing 0 < best_ms < INT_MAX filter downstream handles rejection)
# ---------------------------------------------------------------------------


def test_enrich_raw_sentinel_zero_passed_through():
    p = _pipeline(valid_laps_only=False)
    result = p._enrich_raw({"iBestTime": 0, "iLastTime": 0})
    # best_ms=0 will be rejected by the downstream `0 < best_ms < INT_MAX` guard.
    assert result["best_ms"] == 0


def test_enrich_raw_sentinel_int_max_passed_through():
    p = _pipeline(valid_laps_only=False)
    result = p._enrich_raw({"iBestTime": INT_MAX, "iLastTime": INT_MAX})
    # best_ms=INT_MAX will be rejected by the downstream guard.
    assert result["best_ms"] == INT_MAX


# ---------------------------------------------------------------------------
# 6. _is_new_best — stateful min-comparison is flag-agnostic
# ---------------------------------------------------------------------------


def test_is_new_best_first_tick_always_passes():
    p = _pipeline(valid_laps_only=True)
    state = _FakeState()
    assert p._is_new_best({"best_ms": 90000}, state) is True


def test_is_new_best_same_value_rejected():
    p = _pipeline(valid_laps_only=False)
    state = _FakeState({_LAST_BEST_KEY: 90000})
    assert p._is_new_best({"best_ms": 90000}, state) is False


def test_is_new_best_slower_rejected():
    p = _pipeline(valid_laps_only=True)
    state = _FakeState({_LAST_BEST_KEY: 88000})
    assert p._is_new_best({"best_ms": 89000}, state) is False


def test_is_new_best_faster_passes_and_updates_state():
    p = _pipeline(valid_laps_only=False)
    state = _FakeState({_LAST_BEST_KEY: 90000})
    assert p._is_new_best({"best_ms": 88000}, state) is True
    assert state.get(_LAST_BEST_KEY) == 88000
