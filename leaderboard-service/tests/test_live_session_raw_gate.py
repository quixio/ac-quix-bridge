"""Regression tests for the raw-feed gate on the live-session flag.

Bug: on (re)deploy with no live feed, the leaderboard's live-session
indicator showed "live" for up to `live_session_stale_after_s` (600 s).
Root cause: the session + config topics are rewound to OFFSET_BEGINNING on
startup, so a retained announcement replays and `_adopt_live_session()`
adopts a session even though no raw telemetry is flowing. The fix gates
`current_live_session()` (and `sweep_stale_live_session()`) on a recent
RAW tick (`raw_liveness_window_s`, default 15 s).

These tests poke module-level state directly — the service has no Kafka in
the test environment and `live_stream.publish_live_session` is a no-op when
the WS event loop isn't running, so `_adopt_live_session` is safe to call.
"""

from __future__ import annotations

import os
import time

import pytest

# `get_settings()` (consulted by the raw-liveness window helper) builds the
# full Settings model, which requires Mongo + Quix env vars. Provide dummy
# values before importing the modules so settings construction succeeds in
# the test environment.
os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-token")
os.environ.setdefault("CONFIG_API_URL", "http://localhost:8001")

from api import live_telemetry  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_live_session_state():
    """Isolate each test: clear adopted session + raw clock, restore after."""
    saved_session = live_telemetry._live_session
    saved_tick = live_telemetry._last_raw_tick_epoch
    saved_envelope = live_telemetry._last_live_session_envelope
    live_telemetry._live_session = None
    live_telemetry._last_raw_tick_epoch = 0.0
    live_telemetry._last_live_session_envelope = None
    try:
        yield
    finally:
        live_telemetry._live_session = saved_session
        live_telemetry._last_raw_tick_epoch = saved_tick
        live_telemetry._last_live_session_envelope = saved_envelope


def test_announcement_without_raw_tick_is_not_live():
    """(a) Announcement adopted, NO raw tick → no live session.

    Reproduces the redeploy phantom: a rewound session announcement is
    adopted but raw never flows. The live flag must stay off.
    """
    live_telemetry._adopt_live_session("simpc-1", "nurburgring", "ferrari_488")

    # Adopted record exists internally...
    assert live_telemetry._live_session is not None
    # ...but the gated accessor reports nothing live (no raw tick).
    assert live_telemetry.current_live_session() is None


def test_announcement_with_recent_raw_tick_is_live():
    """(b) Announcement + recent raw tick → live session with metadata."""
    live_telemetry._adopt_live_session("simpc-1", "nurburgring", "ferrari_488")
    # Simulate a raw tick landing now (what `_record_message` stamps).
    live_telemetry._last_raw_tick_epoch = time.time()

    sess = live_telemetry.current_live_session()
    assert sess is not None
    assert sess["hostname"] == "simpc-1"
    assert sess["track"] == "nurburgring"
    assert sess["car"] == "ferrari_488"


def test_live_session_clears_when_raw_tick_ages_out():
    """(c) Raw tick older than `raw_liveness_window_s` → no live session."""
    live_telemetry._adopt_live_session("simpc-1", "nurburgring", "ferrari_488")

    window = live_telemetry._raw_liveness_window_s()
    # Last raw tick is older than the window (announcement still within TTL).
    live_telemetry._last_raw_tick_epoch = time.time() - (window + 1.0)

    assert live_telemetry.current_live_session() is None


def test_record_message_stamps_raw_tick_and_makes_live():
    """End-to-end: a valid raw payload through `_record_message` flips the
    gate to live for a previously-adopted session (the real wiring)."""
    live_telemetry._adopt_live_session("simpc-1", "nurburgring", "ferrari_488")
    assert live_telemetry.current_live_session() is None

    before = live_telemetry._last_raw_tick_epoch
    live_telemetry._record_message(
        {
            "track": "nurburgring",
            "carModel": "ferrari_488",
            "driver": "tomas",
            "iCurrentTime": 12345,
            "completedLaps": 1,
            "normalizedCarPosition": 0.42,
        }
    )
    assert live_telemetry._last_raw_tick_epoch > before
    assert live_telemetry.current_live_session() is not None


def test_record_message_ignores_incomplete_payload():
    """A payload missing track/car/driver must NOT stamp the raw clock —
    only genuine raw ticks count toward liveness."""
    before = live_telemetry._last_raw_tick_epoch
    live_telemetry._record_message({"iCurrentTime": 1})  # no track/car/driver
    assert live_telemetry._last_raw_tick_epoch == before


def test_adopt_without_raw_does_not_broadcast_live_envelope(monkeypatch):
    """Adopt-time broadcast gate: a session announcement adopted with NO raw
    tick (the retained "Lamborghini Huracan" replay on the startup rewind)
    must NOT broadcast a non-null `live_session` envelope to connected
    clients. The metadata record is still kept (labels the flag the moment
    raw resumes), but the active-stream button stays off.
    """
    from api import live_stream

    published: list[dict] = []
    monkeypatch.setattr(
        live_stream, "publish_live_session", lambda env: published.append(env)
    )

    live_telemetry._adopt_live_session("simpc-1", "monza", "lamborghini_huracan_gt3")

    # Record kept internally (ready to label once raw flows)...
    assert live_telemetry._live_session is not None
    # ...but NO live envelope was broadcast (raw not flowing), and the gated
    # accessor agrees nothing is live.
    non_null = [e for e in published if e.get("track") is not None]
    assert non_null == []
    assert live_telemetry.current_live_session() is None


def test_adopt_with_raw_broadcasts_live_envelope(monkeypatch):
    """Companion to the gate test: with a recent raw tick, adopting DOES
    broadcast a non-null `live_session` envelope carrying the combo."""
    from api import live_stream

    published: list[dict] = []
    monkeypatch.setattr(
        live_stream, "publish_live_session", lambda env: published.append(env)
    )
    # Raw is flowing as of now.
    live_telemetry._last_raw_tick_epoch = time.time()

    live_telemetry._adopt_live_session("simpc-1", "monza", "ferrari_488")

    non_null = [e for e in published if e.get("track") is not None]
    assert len(non_null) == 1
    assert non_null[0]["track"] == "monza"
    assert non_null[0]["car"] == "ferrari_488"
