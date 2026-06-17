"""Regression tests: the leaderboard stays live + crash-proof when the lake
is slow or hard-down (spec: lake-resilience).

Two contracts are exercised:

  1. **Bounded retry in `LakehouseClient.query`.** A transient failure
     (`httpx.ReadTimeout`) on the first POST is retried; a success on the
     second attempt yields a parsed DataFrame — the caller never sees the
     timeout.

  2. **Live stream is lake-independent.** With every lake query timing out,
     the best-laps refresh path must NOT raise (stale-on-error), and the
     raw-driven live path (`_record_message` → `publish_snapshot`,
     `current_live_session`) must keep working — proving a hung lake can
     never stall the live active-driver updates.

These tests poke module-level state directly: the service has no Kafka / lake
in the test environment, and `live_stream.*` publishers are no-ops when the
WS event loop isn't running, so the live-path helpers are safe to call.
"""

from __future__ import annotations

import os

import httpx
import pytest

# `get_settings()` builds the full Settings model (Mongo + Quix env vars) and
# the lake-credentials gate. Provide dummy values + real-mode lake creds so
# the refresh path gets past its creds gate and actually attempts a query.
os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-token")
os.environ.setdefault("CONFIG_API_URL", "http://localhost:8001")
os.environ.setdefault("Quix__Lakehouse__Query__Url", "http://lake.invalid")
os.environ.setdefault("LAKE_API_TOKEN", "test-lake-token")

from api import lakehouse_client, live_telemetry  # noqa: E402


# ---------------------------------------------------------------------------
# 1. LakehouseClient.query bounded retry
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal stand-in for `httpx.Response` (only what `query` reads)."""

    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:  # always 200 in these tests
        return None


class _FakeClient:
    """`httpx.Client` context-manager replacement whose `.post` is scripted.

    `behaviours` is a list of callables; each call to `.post` pops the next
    one and either raises it (if it's an exception) or returns it.
    """

    def __init__(self, behaviours: list[object]) -> None:
        self._behaviours = behaviours

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def post(self, *_a: object, **_k: object) -> _FakeResponse:
        outcome = self._behaviours.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, _FakeResponse)
        return outcome


def test_query_retries_then_succeeds(monkeypatch):
    """First POST raises ReadTimeout, second returns a CSV body → `query`
    parses the body and returns the DataFrame (no exception propagates)."""
    behaviours: list[object] = [
        httpx.ReadTimeout("simulated lake timeout"),
        _FakeResponse("driver,ms\nludvik,119054\n"),
    ]

    def _fake_client_factory(*_a, **_k):
        return _FakeClient(behaviours)

    monkeypatch.setattr(lakehouse_client.httpx, "Client", _fake_client_factory)
    # Skip the real backoff sleep so the test is instant.
    monkeypatch.setattr(lakehouse_client.time, "sleep", lambda *_a: None)

    client = lakehouse_client.LakehouseClient(
        base_url="http://lake.invalid", token="t"
    )
    df = client.query("SELECT 1")
    assert list(df["driver"]) == ["ludvik"]
    assert list(df["ms"]) == [119054]
    # Both scripted behaviours were consumed (retry actually happened).
    assert behaviours == []


def test_query_exhausts_retries_then_raises(monkeypatch):
    """Every attempt times out → `query` raises the timeout AFTER exhausting
    the bounded retry budget (callers catch this for stale-on-error)."""
    timeouts: list[object] = [httpx.ReadTimeout("t") for _ in range(5)]

    monkeypatch.setattr(
        lakehouse_client.httpx, "Client", lambda *_a, **_k: _FakeClient(timeouts)
    )
    monkeypatch.setattr(lakehouse_client.time, "sleep", lambda *_a: None)

    client = lakehouse_client.LakehouseClient(
        base_url="http://lake.invalid", token="t"
    )
    with pytest.raises(httpx.ReadTimeout):
        client.query("SELECT 1")
    # Exactly len(_RETRY_BACKOFFS_S)+1 attempts were made.
    expected_attempts = len(lakehouse_client._RETRY_BACKOFFS_S) + 1
    assert len(timeouts) == 5 - expected_attempts


def test_non_retryable_query_error_not_retried(monkeypatch):
    """A LakehouseQueryError (engine SQL error, HTTP 200) is NOT a transient
    failure: a single POST returns the `# ERROR:` body and `query` raises
    immediately — no retry, no wasted attempts."""
    err_body = "\n# ERROR: Binder Error: no such column\n"
    behaviours: list[object] = [_FakeResponse(err_body)]
    monkeypatch.setattr(
        lakehouse_client.httpx, "Client", lambda *_a, **_k: _FakeClient(behaviours)
    )
    monkeypatch.setattr(lakehouse_client.time, "sleep", lambda *_a: None)

    client = lakehouse_client.LakehouseClient(
        base_url="http://lake.invalid", token="t"
    )
    with pytest.raises(lakehouse_client.LakehouseQueryError):
        client.query("SELECT bad")
    assert behaviours == []  # exactly one POST


# ---------------------------------------------------------------------------
# 2. Live stream is unaffected when every lake call times out
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_state():
    """Isolate module-level caches touched by the live + refresh paths."""
    saved = {
        "best_laps_cache": live_telemetry._best_laps_cache,
        "live_session": live_telemetry._live_session,
        "tick": live_telemetry._last_raw_tick_epoch,
        "state": dict(live_telemetry._state),
        "session_cache": dict(live_telemetry._session_cache),
        "envelope": live_telemetry._last_live_session_envelope,
    }
    live_telemetry._best_laps_cache = None
    live_telemetry._live_session = None
    live_telemetry._last_raw_tick_epoch = 0.0
    live_telemetry._state.clear()
    live_telemetry._session_cache.clear()
    live_telemetry._last_live_session_envelope = None
    try:
        yield
    finally:
        live_telemetry._best_laps_cache = saved["best_laps_cache"]
        live_telemetry._live_session = saved["live_session"]
        live_telemetry._last_raw_tick_epoch = saved["tick"]
        live_telemetry._state.clear()
        live_telemetry._state.update(saved["state"])
        live_telemetry._session_cache.clear()
        live_telemetry._session_cache.update(saved["session_cache"])
        live_telemetry._last_live_session_envelope = saved["envelope"]


def _always_timeout(*_a, **_k):
    raise httpx.ReadTimeout("simulated lake hard-down")


def test_refresh_does_not_raise_when_lake_always_times_out(monkeypatch, _reset_state):
    """A best-laps refresh whose every lake query times out must NOT raise out
    of the refresh path — it swallows the error and keeps the previous cache.
    """
    # Force a known group so the refresh actually attempts a query.
    monkeypatch.setattr(
        live_telemetry,
        "_known_groups",
        lambda: [("nurburgring", "ferrari_488", "baseline", "rig-a")],
    )
    # Every Query A / discovery call times out (after the client's own retries,
    # which we don't need to exercise here — patch at the query-fn seam).
    from api.routes import leaderboard_real

    monkeypatch.setattr(leaderboard_real, "_query_best_laps_min", _always_timeout)

    # Must not raise. Cache stays at its cold-start sentinel (None) or empty.
    live_telemetry.refresh_best_laps_cache(
        "http://lake.invalid", "tok", force=True
    )
    # The group's failure was recorded for backoff (stale-on-error bookkeeping).
    assert ("nurburgring", "ferrari_488", "baseline", "rig-a") in (
        live_telemetry._best_laps_failure_ts
    )


def test_live_snapshot_publishes_with_lake_down(monkeypatch, _reset_state):
    """With every lake call timing out, the RAW-driven live path keeps
    working: `_record_message` publishes a snapshot and `current_live_session`
    reports the session as live. The live stream is lake-independent.
    """
    # Sabotage every lake seam the live path could conceivably touch.
    from api import partition_index
    from api.routes import leaderboard_real

    monkeypatch.setattr(leaderboard_real, "_query_best_laps_min", _always_timeout)
    monkeypatch.setattr(partition_index, "enumerate_groups", _always_timeout)
    # The hot path uses the lake-free cached read; force it cold so the test
    # proves the tick proceeds even with no partition data available.
    monkeypatch.setattr(partition_index, "cached_groups", lambda: None)

    # Capture what the live path hands to the WS broadcaster.
    published: list[dict] = []
    from api import live_stream

    monkeypatch.setattr(
        live_stream, "publish_snapshot", lambda snap: published.append(snap)
    )
    # Adopt a session + open the raw gate so current_live_session() is live.
    monkeypatch.setattr(live_stream, "publish_live_session", lambda *_a, **_k: None)

    live_telemetry._adopt_live_session("simpc-1", "nurburgring", "ferrari_488")

    # Feed a fully-enriched raw tick straight into _record_message (this is the
    # live active-driver path; it never touches the lake).
    live_telemetry._record_message(
        {
            "track": "nurburgring",
            "carModel": "ferrari_488",
            "driver": "ludvik",
            "experiment": "baseline",
            "environment": "rig-a",
            "iCurrentTime": 42_000,
            "completedLaps": 1,
            "normalizedCarPosition": 0.5,
            "iLastTime": 0,
            "iBestTime": 0,
        }
    )

    # Live snapshot published despite the lake being down. The driver name is
    # the title-cased fallback ("Ludvik") because the Mongo display-name
    # lookup is empty in the test env — folding/casing is orthogonal to lake
    # health; what matters is that a snapshot was published at all.
    assert published, "expected a live snapshot to be published"
    assert published[-1]["driver"].lower() == "ludvik"
    assert published[-1]["track"] == "nurburgring"

    # And the live session is reported live (raw tick just stamped the clock).
    sess = live_telemetry.current_live_session()
    assert sess is not None
    assert sess["track"] == "nurburgring"
    assert sess["car"] == "ferrari_488"
