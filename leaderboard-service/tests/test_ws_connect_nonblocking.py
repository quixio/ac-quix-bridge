"""Regression tests: the WebSocket connect path never blocks on the lake.

Behavior contract (dev-planning/leaderboard-consolidated, live-first fix):
the leaderboard's live view must paint immediately from the WS stream and
the (possibly empty) in-process caches, without ever awaiting the slow lake
partition enumeration (`partition_index.enumerate_groups`, 30 s timeout) or
the synchronous best-laps refresh. Historical/DB data hydrates async.

These tests poke module-level state directly — there is no Kafka / lake in
the test environment, so a lake call would raise / hang rather than answer.
We assert the connect-path helpers never reach for it.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-token")
os.environ.setdefault("CONFIG_API_URL", "http://localhost:8001")
# Real-mode credentials so build_live_positions gets past the creds gate
# and exercises the cold-cache branch we care about.
os.environ.setdefault("Quix__Lakehouse__Query__Url", "http://lake.invalid")
os.environ.setdefault("LAKE_API_TOKEN", "test-lake-token")

from api import live_telemetry  # noqa: E402


def _import_leaderboard_real():
    """Import the assembly module lazily, skipping the test if its pymongo
    dependency isn't present in the lightweight unit-test venv.

    The existing suite imports only `live_telemetry` to stay pymongo-free;
    the two `build_live_positions` cases here need the assembly module, so
    they opt in individually rather than gating the whole (lake-free)
    fast-envelope suite on pymongo.
    """
    pytest.importorskip("pymongo", reason="pymongo not installed in test venv")
    from api.routes import leaderboard_real

    return leaderboard_real


@pytest.fixture(autouse=True)
def _reset_state():
    """Isolate each test: clear the best-laps cache + adopted session."""
    saved_cache = live_telemetry._best_laps_cache
    saved_session = live_telemetry._live_session
    saved_tick = live_telemetry._last_raw_tick_epoch
    saved_exp = dict(live_telemetry._experiment_cache)
    live_telemetry._best_laps_cache = None  # cold
    live_telemetry._live_session = None
    live_telemetry._last_raw_tick_epoch = 0.0
    try:
        yield
    finally:
        live_telemetry._best_laps_cache = saved_cache
        live_telemetry._live_session = saved_session
        live_telemetry._last_raw_tick_epoch = saved_tick
        live_telemetry._experiment_cache.clear()
        live_telemetry._experiment_cache.update(saved_exp)


def test_cold_cache_ws_path_serves_empty_without_lake(monkeypatch):
    """`allow_cold_refresh=False` on a cold cache must return `[]` and never
    trigger the synchronous lake refresh (the 30 s blocking call)."""

    def _boom(*_a, **_k):
        raise AssertionError("refresh_best_laps_cache must NOT run on WS connect")

    leaderboard_real = _import_leaderboard_real()
    monkeypatch.setattr(live_telemetry, "refresh_best_laps_cache", _boom)
    # Mongo is unused when the cache is empty (no driver-name resolution),
    # but build_live_positions calls _build_driver_name_lookup(mongo) — pass a
    # stub whose .drivers.find returns nothing.
    rows = leaderboard_real.build_live_positions(_StubMongo(), allow_cold_refresh=False)
    assert rows == []


def test_cold_cache_http_path_still_refreshes(monkeypatch):
    """Default `allow_cold_refresh=True` (the polled /live-positions path)
    keeps the synchronous cold-cache refresh — proving the WS gate is opt-in
    and doesn't regress the HTTP fallback."""
    leaderboard_real = _import_leaderboard_real()
    called = {"n": 0}

    def _mark_refreshed(*_a, **_k):
        called["n"] += 1
        # Simulate a refresh that found no groups → empty (not None) cache.
        live_telemetry._best_laps_cache = {}

    monkeypatch.setattr(live_telemetry, "refresh_best_laps_cache", _mark_refreshed)
    rows = leaderboard_real.build_live_positions(_StubMongo())
    assert called["n"] == 1
    assert rows == []


def test_fast_live_session_envelope_skips_partition_index(monkeypatch):
    """`current_live_session_envelope_fast` resolves experiment from the DCM
    cache only — it must never call `partition_index.enumerate_groups`."""
    from api import partition_index

    def _boom():
        raise AssertionError("enumerate_groups must NOT run on the fast path")

    # Seed the adopted-session record + a fresh raw tick directly (NOT via
    # `_adopt_live_session`, which broadcasts through the lake-resolving
    # path — that runs on the consumer thread in production, off connect).
    import time as _t

    live_telemetry._live_session = {
        "hostname": "simpc-1",
        "track": "nurburgring",
        "car": "ferrari_488",
        "last_seen_epoch": _t.time(),
    }
    live_telemetry._last_raw_tick_epoch = _t.time()

    # Patch AFTER seeding so only the fast-envelope call under test is guarded.
    monkeypatch.setattr(partition_index, "enumerate_groups", _boom)
    with live_telemetry._state_lock:
        live_telemetry._experiment_cache["simpc-1"] = {
            "experiment": "baseline",
            "environment": "rig-a",
            "driver": "tomas",
            "updated_epoch": _t.time(),
        }

    env = live_telemetry.current_live_session_envelope_fast()
    assert env["type"] == "live_session"
    assert env["track"] == "nurburgring"
    assert env["car"] == "ferrari_488"
    assert env["experiment"] == "baseline"
    assert env["environment"] == "rig-a"


def test_fast_live_session_envelope_null_when_no_session(monkeypatch):
    """No live session → all-null fast envelope, still lake-free."""
    from api import partition_index

    monkeypatch.setattr(
        partition_index,
        "enumerate_groups",
        lambda: (_ for _ in ()).throw(AssertionError("no lake on fast path")),
    )
    env = live_telemetry.current_live_session_envelope_fast()
    assert env == {
        "type": "live_session",
        "track": None,
        "car": None,
        "experiment": None,
        "environment": None,
    }


class _StubMongo:
    """Minimal Mongo stand-in: `.drivers.find(...)` yields nothing."""

    class _Coll:
        def find(self, *_a, **_k):
            return iter(())

    drivers = _Coll()
