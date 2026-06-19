"""WebSocket broadcaster for the leaderboard live stream.

Background
==========

The leaderboard frontend is now fully WebSocket-driven: there is no
polling fallback in the UI. The server pushes two kinds of messages
through the same socket:

* ``{"type": "snapshot", "rows": [LivePositionEntry, ...]}`` — a flat
  list with the same shape ``/api/v1/leaderboard/live-positions``
  returns. Sent once on connect and again whenever the gate-vectors
  cache refreshes (i.e. historicals changed).
* ``{"type": "active", "row": {...}}`` — a per-tick mutation of the
  active driver row. Same per-driver fields the previous
  "raw" mutation envelope carried.

Two cross-thread hooks bridge the Kafka consumer thread to the FastAPI
event loop:

* ``publish_snapshot`` (active row, hot path, ~60 Hz in → ~20 Hz out
  through the latest-wins queue).
* ``publish_full_snapshot`` (full rows list, rare path, no throttle —
  forwarded directly to the broadcaster which fans out immediately).

Why a separate module
---------------------

* ``live_telemetry`` is the Kafka-thread side — sync, owns ``_state``.
* ``live_stream`` is the FastAPI-async side — owns the event loop,
  the client set, the queue, and the broadcaster task.

Mixing the two inside ``live_telemetry`` would force every reader of
``_state`` to also reason about asyncio. Keeping them split means
``live_telemetry`` stays pure-sync (Kafka loop is a daemon thread) and
``live_stream`` stays pure-async (lives on the FastAPI event loop).

Every cross-boundary call uses ``asyncio.run_coroutine_threadsafe`` on
the captured loop. Any failure (loop not running, queue full) is
swallowed so the consumer loop never crashes.

Throttling
----------

``THROTTLE_MS = 50`` keeps the active-mutation wire at ≤20 Hz. Full
snapshots bypass this throttle: they're rare (once per session change
or new historical) and the client needs to react immediately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# Maximum broadcast rate (per client). 50 ms ≈ 20 Hz — fast enough for a
# smooth lap clock, slow enough to spare React + the wire.
THROTTLE_MS = 50

# Diagnostic-logging throttle (logging only, no control-flow impact).
# ``publish_snapshot`` is a hot path (~20 Hz out), so the snapshot INFO line
# is gated to ~once/second. ``publish_live_session`` is low-frequency and is
# NOT throttled.
_LAST_SNAPSHOT_LOG_EPOCH: float = 0.0
_SNAPSHOT_LOG_INTERVAL_S: float = 1.0


# Idle keepalive interval. Quix ingress (and most cloud LBs) drop idle WS
# connections after ~30–60 s; AC isn't always sending fast enough to keep
# the pipe warm. We broadcast a tiny `{"type": "ping"}` envelope every
# 25 s so the socket always has recent traffic. The frontend ignores the
# envelope. Cheap (one short JSON frame per client per 25 s).
PING_INTERVAL_SECONDS = 25.0


# ---------------------------------------------------------------------------
# Module-level async state
# ---------------------------------------------------------------------------
#
# All of these are written/read on the FastAPI event loop except where
# explicitly noted (publish_snapshot is the only cross-thread entry).

# Captured during lifespan startup. ``None`` outside the FastAPI lifespan,
# which is also the case during pytest collection of unrelated modules.
_loop: asyncio.AbstractEventLoop | None = None

# Single-slot "latest snapshot" buffer. We use a Queue rather than a plain
# attribute because the broadcaster task needs an await-point to block on
# until something arrives. ``maxsize=1`` makes it a one-element ring with
# explicit overflow handling in ``_enqueue_snapshot`` (drop oldest, keep
# newest). Initialised lazily because ``asyncio.Queue`` must be created
# inside a running event loop.
_queue: asyncio.Queue[dict[str, Any]] | None = None

# Connected WebSocket clients. Mutated only from coroutines on the
# FastAPI event loop (the WS endpoint and the broadcaster), so a plain
# set + asyncio.Lock is sufficient — no threading primitives needed here.
_clients: set[WebSocket] = set()
_clients_lock: asyncio.Lock | None = None

# Background task handle so the lifespan can cancel it on shutdown.
_broadcaster_task: asyncio.Task[None] | None = None

# Background keepalive-ping task handle. Independent of the active-mutation
# broadcaster so a stalled snapshot queue doesn't also stop the pings.
_keepalive_task: asyncio.Task[None] | None = None

# LOCAL_DEV_MODE-only sim driver task: periodically pumps an active
# mutation + active_state envelope so the dev page shows live behaviour
# without a Kafka consumer. `None` outside LOCAL_DEV_MODE.
_sim_driver_task: asyncio.Task[None] | None = None

# Tracks the last (driver, track, car, experiment) combo the sim
# advertised in `active_state` so the loop only emits transitions, not
# every tick.
_sim_last_active_state: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Lifespan hooks (called from api/app.py)
# ---------------------------------------------------------------------------


async def start_broadcaster() -> None:
    """Capture the current event loop and start the broadcaster task.

    Idempotent: re-calling while a broadcaster is already running is a
    no-op, so a hot-reloaded FastAPI startup doesn't fork ghost tasks.

    In LOCAL_DEV_MODE we additionally start a sim-driver task that
    publishes periodic active mutations + an initial active-state
    envelope — the Kafka consumer is disabled there, so without this
    the dev page would see a single snapshot then silence.
    """
    global _loop, _queue, _clients_lock, _broadcaster_task, _keepalive_task
    global _sim_driver_task

    if _broadcaster_task is not None and not _broadcaster_task.done():
        return

    _loop = asyncio.get_running_loop()
    _queue = asyncio.Queue(maxsize=1)
    _clients_lock = asyncio.Lock()
    _broadcaster_task = asyncio.create_task(
        _broadcaster_loop(), name="live-stream-broadcaster"
    )
    _keepalive_task = asyncio.create_task(
        _keepalive_loop(), name="live-stream-keepalive"
    )
    import os

    if os.getenv("LOCAL_DEV_MODE", "false").lower() == "true":
        _sim_driver_task = asyncio.create_task(
            _sim_driver_loop(), name="live-stream-sim-driver"
        )
    logger.info(
        "live-stream broadcaster started (throttle=%d ms, ping=%.1f s)",
        THROTTLE_MS,
        PING_INTERVAL_SECONDS,
    )


async def stop_broadcaster() -> None:
    """Cancel the broadcaster task and close every open WebSocket.

    Called from the FastAPI lifespan shutdown hook. Bounded: every step
    has a small timeout/try-suppress so a misbehaving client can't stall
    process exit.
    """
    global _broadcaster_task, _keepalive_task

    if _broadcaster_task is not None:
        _broadcaster_task.cancel()
        try:
            await _broadcaster_task
        except (asyncio.CancelledError, Exception):
            pass
        _broadcaster_task = None

    if _keepalive_task is not None:
        _keepalive_task.cancel()
        try:
            await _keepalive_task
        except (asyncio.CancelledError, Exception):
            pass
        _keepalive_task = None

    global _sim_driver_task
    if _sim_driver_task is not None:
        _sim_driver_task.cancel()
        try:
            await _sim_driver_task
        except (asyncio.CancelledError, Exception):
            pass
        _sim_driver_task = None

    # Close every still-connected client. Use a snapshot of the set so a
    # client closing itself mid-iteration doesn't break the loop.
    if _clients_lock is not None:
        async with _clients_lock:
            snapshot = list(_clients)
            _clients.clear()
    else:
        snapshot = list(_clients)
        _clients.clear()
    for ws in snapshot:
        try:
            await ws.close()
        except Exception:
            pass
    logger.info("live-stream broadcaster stopped")


# ---------------------------------------------------------------------------
# Client registration (called from the WS endpoint)
# ---------------------------------------------------------------------------


async def register(websocket: WebSocket) -> None:
    """Add an accepted WebSocket to the broadcast set."""
    if _clients_lock is None:
        # Broadcaster never started — treat as no-op rather than crash.
        # In practice this only happens in tests that hit the WS endpoint
        # without going through the lifespan.
        return
    async with _clients_lock:
        _clients.add(websocket)
    logger.info("live-stream client connected (%d total)", len(_clients))


async def unregister(websocket: WebSocket) -> None:
    """Drop a WebSocket from the broadcast set (idempotent)."""
    if _clients_lock is None:
        return
    async with _clients_lock:
        _clients.discard(websocket)
    logger.info("live-stream client disconnected (%d remaining)", len(_clients))


# ---------------------------------------------------------------------------
# Cross-thread snapshot publishers (called from the Kafka consumer thread)
# ---------------------------------------------------------------------------


def publish_snapshot(snapshot: dict[str, Any]) -> None:
    """Hand the latest active-driver snapshot to the FastAPI event loop.

    Called from the Kafka consumer thread after ``_record_message``
    finishes updating ``_state``. Schedules a coroutine on the captured
    event loop via ``run_coroutine_threadsafe`` — the same pattern used
    by ``telemetry-dashboard/main.py:push_to_clients``.

    All failure modes are swallowed: the consumer loop must NEVER die
    because the FastAPI loop happens to be down (e.g. during shutdown).

    The ``snapshot`` dict is the same shape ``live_telemetry.get_active_driver``
    returns, mapped to the ``{"type": "active", ...}`` wire envelope in
    ``_broadcaster_loop``.
    """
    if _loop is None or _queue is None:
        return
    # Diagnostic (logging only): a snapshot is being handed to the broadcaster.
    # Throttled to ~once/second so the ~20 Hz hot path can't flood the logs.
    global _LAST_SNAPSHOT_LOG_EPOCH
    _now = time.time()
    if _now - _LAST_SNAPSHOT_LOG_EPOCH >= _SNAPSHOT_LOG_INTERVAL_S:
        _LAST_SNAPSHOT_LOG_EPOCH = _now
        logger.info(
            "publish snapshot -> WS: driver=%r track=%r car=%r last_gate=%s clients=%d",
            snapshot.get("driver"),
            snapshot.get("track"),
            snapshot.get("car"),
            snapshot.get("last_gate_index"),
            len(_clients),
        )
    try:
        asyncio.run_coroutine_threadsafe(_enqueue_snapshot(snapshot), _loop)
    except RuntimeError:
        # Loop is closed (shutdown race). Silent drop.
        pass
    except Exception:
        # Any other scheduler failure — log but don't propagate.
        logger.debug("publish_snapshot scheduling failed", exc_info=True)


def publish_full_snapshot(rows: list[dict[str, Any]]) -> None:
    """Broadcast a full leaderboard snapshot to every connected client.

    Bypasses the throttled active-mutation queue: full snapshots are
    rare (one per gate-vectors cache refresh — i.e. once per AC session
    change) and the client must re-render immediately to reflect the
    new historicals.

    ``rows`` is the same flat list of ``LivePositionEntry``-shaped dicts
    ``leaderboard_real.build_live_positions()`` returns. The list
    is wrapped in ``{"type": "snapshot", "rows": [...]}`` and sent as
    one JSON message to every WebSocket client. Identical shape to what
    a freshly-connected client receives on connect (see
    ``send_initial_snapshot``), so the frontend has a single code path
    for both cases.

    Called from the Kafka consumer thread. Failures are swallowed — the
    consumer loop must not crash on a shutdown race or a slow socket.
    """
    if _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast_full_snapshot(rows), _loop)
    except RuntimeError:
        # Loop is closed (shutdown race). Silent drop.
        pass
    except Exception:
        logger.debug("publish_full_snapshot scheduling failed", exc_info=True)


def publish_active_state(envelope: dict[str, Any]) -> None:
    """Broadcast one ``{"type": "active_state", ...}`` envelope.

    Bypasses the active-mutation throttled queue — active-state
    transitions are rare (once per session start/end) and the frontend
    has to react immediately (toggle visibility, dropdown disable).

    Called from the Kafka consumer thread by
    ``live_telemetry._update_active_state`` whenever the canonical
    active state changes. Failures are swallowed.
    """
    if _loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast_envelope(envelope), _loop)
    except RuntimeError:
        pass
    except Exception:
        logger.debug("publish_active_state scheduling failed", exc_info=True)


def publish_live_session(envelope: dict[str, Any]) -> None:
    """Broadcast one ``{"type": "live_session", ...}`` envelope.

    Same shape and delivery semantics as ``publish_active_state``:
    bypasses the throttled queue (transitions are rare — session adopt /
    change / stale-clear) and the frontend reacts immediately (best-laps
    panel combo). Called from the Kafka consumer thread by
    ``live_telemetry._publish_live_session_if_changed``. Failures are
    swallowed.
    """
    if _loop is None:
        return
    # Diagnostic (logging only): a live_session envelope is being broadcast.
    # Low-frequency (session adopt / change / stale-clear), so NOT throttled.
    logger.info("publish live_session -> WS: %s", envelope)
    try:
        asyncio.run_coroutine_threadsafe(_broadcast_envelope(envelope), _loop)
    except RuntimeError:
        pass
    except Exception:
        logger.debug("publish_live_session scheduling failed", exc_info=True)


async def _sim_driver_loop() -> None:
    """LOCAL_DEV_MODE: emit periodic active mutations + active_state.

    Without this the dev page would receive one snapshot at connect and
    then silence — no rank movement, no colour cycling. We pump the
    simulator at 4 Hz (250 ms) so the lap clock advances visibly and
    the active row colour transitions are observable. The active-state
    envelope fires once when the first driver appears and again on a
    combo change.
    """
    # Lazy import to keep the module import graph one-way.
    from .routes import live_positions_sim

    interval_s = 0.25
    try:
        while True:
            try:
                await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                raise
            try:
                rows = live_positions_sim.make_local_dev_live_positions()
            except Exception:
                logger.debug("sim driver: snapshot build failed", exc_info=True)
                continue
            # Find the active row for the first (track, car, experiment)
            # combo in the sim — there are several, but the dev page
            # filters down to a single combo via dropdowns anyway. The
            # WS contract carries every active mutation; the frontend
            # picks the relevant one by `(driver, track, car, experiment)`.
            active_rows = [r for r in rows if r.get("is_active")]
            if not active_rows:
                continue
            # Pump one active mutation per active row.
            for active in active_rows:
                snapshot = {
                    "driver": str(active.get("driver") or ""),
                    "track": str(active.get("track") or ""),
                    "car": str(active.get("car") or ""),
                    "experiment": str(active.get("experiment") or ""),
                    "current_lap": active.get("current_lap"),
                    "current_lap_time_ms": active.get("current_lap_time_ms") or 0,
                    "normalized_position": 0.0,
                    "last_gate_index": active.get("last_gate_index"),
                    "last_gate_state": active.get("last_gate_state"),
                    "last_gate_delta_ms": active.get("last_gate_delta_ms"),
                    # No per-historical deltas in sim mutations — the
                    # initial snapshot already populates each historical's
                    # `delta_at_last_gate_ms`, and the sim doesn't
                    # recompute them mid-tick. The colour cue on the
                    # active row is the visible cycling signal.
                    "historical_deltas": {},
                }
                publish_snapshot(snapshot)
            # Active-state envelope on first appearance or combo change.
            first = active_rows[0]
            new_state = {
                "is_active": True,
                "driver": str(first.get("driver") or ""),
                "track": str(first.get("track") or ""),
                "car": str(first.get("car") or ""),
                "experiment": str(first.get("experiment") or ""),
                "environment": None,
            }
            global _sim_last_active_state
            if _sim_last_active_state != new_state:
                _sim_last_active_state = dict(new_state)
                publish_active_state({"type": "active_state", **new_state})
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("sim driver loop crashed")


async def _broadcast_envelope(envelope: dict[str, Any]) -> None:
    """Serialise a tagged envelope and fan out to every client.

    Used for control messages (e.g. ``active_state``) that bypass the
    throttled active-mutation broadcaster. Runs on the FastAPI event
    loop. Drops misformed envelopes silently — same defensive shape as
    ``_broadcast_full_snapshot``.
    """
    try:
        text = json.dumps(envelope, default=str)
    except (TypeError, ValueError):
        logger.exception(
            "envelope JSON serialisation failed; dropping (type=%r)",
            envelope.get("type") if isinstance(envelope, dict) else None,
        )
        return
    await _broadcast(text)


async def _enqueue_snapshot(snapshot: dict[str, Any]) -> None:
    """Put the snapshot in the queue, dropping the oldest if full.

    Single-slot latest-wins semantics. The broadcaster drains one item
    per ``THROTTLE_MS`` window, so the queue depth never exceeds 1 in
    practice — but bursts inside one window still need to collapse.
    """
    if _queue is None:
        return
    # Drop the stale snapshot if the broadcaster hasn't consumed it yet.
    if _queue.full():
        try:
            _queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    try:
        _queue.put_nowait(snapshot)
    except asyncio.QueueFull:
        # Race with another producer (shouldn't happen — consumer is
        # single-threaded — but cheap to guard).
        pass


async def _broadcast_full_snapshot(rows: list[dict[str, Any]]) -> None:
    """Serialise a full snapshot and fan out to every client.

    Runs on the FastAPI event loop. Independent of the broadcaster
    task's throttled active-mutation pipeline, so a snapshot interleaves
    naturally between active-mutation broadcasts without being delayed
    by ``THROTTLE_MS``.
    """
    payload = {"type": "snapshot", "rows": rows}
    try:
        text = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        logger.exception("full-snapshot JSON serialisation failed; dropping")
        return
    await _broadcast(text)


# ---------------------------------------------------------------------------
# Broadcaster task
# ---------------------------------------------------------------------------


async def _broadcaster_loop() -> None:
    """Drain the queue and fan out to every client, throttled.

    Loop body:
      1. Block on ``queue.get()`` until a snapshot arrives.
      2. Serialise to the wire schema (active-driver mutation only).
      3. Broadcast to every client; drop clients whose ``send_text``
         raises.
      4. Sleep ``THROTTLE_MS`` so the next iteration is rate-limited.

    The sleep AFTER the send means the first message of a burst goes
    out immediately and subsequent ones coalesce — preferable to sleep-
    -then-send which would add a fixed 50 ms latency to every message.
    """
    if _queue is None:
        return
    while True:
        try:
            snapshot = await _queue.get()
        except asyncio.CancelledError:
            raise
        payload = _build_wire_payload(snapshot)
        if payload is None:
            continue
        text = json.dumps(payload)
        await _broadcast(text)
        # Throttle: sleep before pulling the next snapshot. Bursts during
        # this window collapse in ``_enqueue_snapshot`` (latest wins).
        try:
            await asyncio.sleep(THROTTLE_MS / 1000.0)
        except asyncio.CancelledError:
            raise


async def _keepalive_loop() -> None:
    """Broadcast a tiny ``{"type": "ping"}`` envelope every ``PING_INTERVAL_SECONDS``.

    Quix ingress (and most cloud LBs) close idle WS connections after
    ~30–60 s of no traffic. Without this loop, a leaderboard tab open
    on a quiet sim (no `active` mutations being published) would see
    repeated disconnect/reconnect storms as the ingress timeouts fire.

    The frontend treats `type=ping` as a no-op (no row mutation, no
    state change). The send goes through ``_broadcast`` so dead clients
    are pruned the same way they are on real broadcasts.

    The same loop also calls ``sweep_stale_active_state`` so an
    `active_state` transition to `is_active=false` reaches the wire
    after AC stops — the consumer thread can't push that transition on
    its own because by definition it stops receiving ticks. The sweep
    is cheap (one dict scan + at most one envelope) so we just run it
    every keepalive tick.
    """
    text = json.dumps({"type": "ping"})
    while True:
        try:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        try:
            # Lazy import — same one-way layering as the publishers.
            from . import live_telemetry

            live_telemetry.sweep_stale_active_state()
            # Same hook clears a live session that outlived its TTL —
            # cheap (one dict check, at most one envelope on transition).
            live_telemetry.sweep_stale_live_session()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("active-state sweep failed", exc_info=True)
        try:
            await _broadcast(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a transient broadcast failure kill the keepalive
            # loop — that would re-introduce the original storm.
            logger.debug("keepalive broadcast failed", exc_info=True)


def _build_wire_payload(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Wrap an active-row mutation in the tagged envelope.

    The inner ``row`` schema is a strict subset of ``LivePositionEntry``:
    only the per-tick mutable fields of the active driver. Historicals,
    rank, and best laps come through the ``"snapshot"`` envelope (sent
    on connect and on gate-vectors refresh).

    `historical_deltas` rides inline on the same envelope (spec §7.2):
    a `{driver_display_name: delta_at_last_gate_ms}` dict the frontend
    applies to each historical row in the matching group. ~10
    historicals × 8 bytes ≈ 80 B per gate crossing — trivial.

    Returns ``None`` for malformed snapshots so the broadcaster can
    skip them without crashing.
    """
    try:
        row = {
            "driver": str(snapshot.get("driver") or ""),
            "track": str(snapshot.get("track") or ""),
            "car": str(snapshot.get("car") or ""),
            "experiment": str(snapshot.get("experiment") or ""),
            "current_lap": (
                int(snapshot["current_lap"])
                if snapshot.get("current_lap") is not None
                else None
            ),
            "current_lap_time_ms": int(snapshot.get("current_lap_time_ms") or 0),
            "normalized_position": float(snapshot.get("normalized_position") or 0.0),
            "last_gate_index": snapshot.get("last_gate_index"),
            "last_gate_state": snapshot.get("last_gate_state"),
            "last_gate_delta_ms": snapshot.get("last_gate_delta_ms"),
        }
        raw_historical_deltas = snapshot.get("historical_deltas") or {}
        if isinstance(raw_historical_deltas, dict):
            historical_deltas = {
                str(k): int(v) for k, v in raw_historical_deltas.items()
            }
        else:
            historical_deltas = {}
        raw_next = snapshot.get("historical_at_positions_next") or {}
        if isinstance(raw_next, dict):
            historical_at_positions_next = {
                str(k): int(v) for k, v in raw_next.items()
            }
        else:
            historical_at_positions_next = {}
        raw_at_crossing = snapshot.get("historical_at_positions_at_crossing") or {}
        if isinstance(raw_at_crossing, dict):
            historical_at_positions_at_crossing = {
                str(k): int(v) for k, v in raw_at_crossing.items()
            }
        else:
            historical_at_positions_at_crossing = {}
    except (TypeError, ValueError):
        return None
    return {
        "type": "active",
        "row": row,
        "historical_deltas": historical_deltas,
        # Renamed from `historical_at_positions` to make the semantics
        # explicit: this is the gate (i*+1) cumulative time used by the
        # frontend in "live" mode (between blue-freeze windows).
        "historical_at_positions_next": historical_at_positions_next,
        # New (spec §4.2): gate (i*) cumulative time used by the frontend
        # during the 3 s blue-freeze immediately after a gate crossing.
        "historical_at_positions_at_crossing": historical_at_positions_at_crossing,
    }


async def _broadcast(text: str) -> None:
    """Send ``text`` to every connected client; drop on failure.

    We take a snapshot of the client set under the lock, then send
    outside the lock so a slow ``send_text`` can't stall registrations.
    Dead clients are collected and removed in a second locked pass.
    """
    if _clients_lock is None:
        return
    async with _clients_lock:
        snapshot = list(_clients)
    if not snapshot:
        return
    dead: list[WebSocket] = []
    for ws in snapshot:
        try:
            await ws.send_text(text)
        except Exception:
            dead.append(ws)
    if dead:
        async with _clients_lock:
            for ws in dead:
                _clients.discard(ws)
