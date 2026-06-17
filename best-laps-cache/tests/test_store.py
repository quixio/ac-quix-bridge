"""Unit tests for the raw->best-lap store and reconcile merge."""

from __future__ import annotations

from best_laps_cache.store import BestLapsStore, make_key, split_key


def test_make_split_key_roundtrip():
    key = make_key("env", "exp", "trk", "car", "Ludvík")
    assert split_key(key) == {
        "environment": "env",
        "experiment": "exp",
        "track": "trk",
        "carModel": "car",
        "driver": "Ludvík",
    }


def test_update_live_monotonic_min():
    s = BestLapsStore()
    assert s.update_live("env", "exp", "trk", "car", "Ada", 91000) is not None
    # Slower lap: no change.
    assert s.update_live("env", "exp", "trk", "car", "Ada", 92000) is None
    # Faster lap: change.
    assert s.update_live("env", "exp", "trk", "car", "Ada", 90000) is not None
    rows = s.query(driver="Ada")
    assert len(rows) == 1
    assert rows[0]["best_lap_ms"] == 90000
    assert rows[0]["source"] == "live"


def test_update_live_skips_non_positive_and_blank():
    s = BestLapsStore()
    assert s.update_live("env", "exp", "trk", "car", "Ada", 0) is None
    assert s.update_live("env", "exp", "trk", "car", "", 90000) is None
    assert len(s) == 0


def test_reconcile_merge_min_policy():
    """Live faster lap is never clobbered by an older/slower DB value (O4)."""
    s = BestLapsStore()
    live_key = make_key("env", "exp", "trk", "car", "Ada")
    s.update_live("env", "exp", "trk", "car", "Ada", 89000)  # live, fast
    # DB has a slower lap for Ada plus a brand-new driver Bo.
    bo_key = make_key("env", "exp", "trk", "car", "Bo")
    changed = s.merge_reconcile({live_key: 91000, bo_key: 95000})
    # Ada unchanged (live faster wins); Bo added.
    assert changed == 1
    ada = s.query(driver="Ada")[0]
    bo = s.query(driver="Bo")[0]
    assert ada["best_lap_ms"] == 89000
    assert ada["source"] == "live"
    assert bo["best_lap_ms"] == 95000
    assert bo["source"] == "reconcile"


def test_reconcile_overwrites_when_db_faster():
    s = BestLapsStore()
    key = make_key("env", "exp", "trk", "car", "Ada")
    s.update_live("env", "exp", "trk", "car", "Ada", 91000)
    changed = s.merge_reconcile({key: 88000})
    assert changed == 1
    assert s.query(driver="Ada")[0]["best_lap_ms"] == 88000


def test_query_filters():
    s = BestLapsStore()
    s.update_live("env1", "exp", "nurburgring", "bmw_1m", "Ada", 91000)
    s.update_live("env1", "exp", "spa", "bmw_1m", "Bo", 92000)
    assert len(s.query()) == 2
    assert len(s.query(track="spa")) == 1
    assert s.query(track="spa")[0]["driver"] == "Bo"
    assert len(s.query(track="nope")) == 0
