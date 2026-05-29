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
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


# Maximum broadcast rate (per client). 50 ms ≈ 20 Hz — fast enough for a
# smooth lap clock, slow enough to spare React + the wire.
THROTTLE_MS = 50


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


# ---------------------------------------------------------------------------
# Lifespan hooks (called from api/app.py)
# ---------------------------------------------------------------------------


async def start_broadcaster() -> None:
    """Capture the current event loop and start the broadcaster task.

    Idempotent: re-calling while a broadcaster is already running is a
    no-op, so a hot-reloaded FastAPI startup doesn't fork ghost tasks.
    """
    global _loop, _queue, _clients_lock, _broadcaster_task

    if _broadcaster_task is not None and not _broadcaster_task.done():
        return

    _loop = asyncio.get_running_loop()
    _queue = asyncio.Queue(maxsize=1)
    _clients_lock = asyncio.Lock()
    _broadcaster_task = asyncio.create_task(
        _broadcaster_loop(), name="live-stream-broadcaster"
    )
    logger.info("live-stream broadcaster started (throttle=%d ms)", THROTTLE_MS)


async def stop_broadcaster() -> None:
    """Cancel the broadcaster task and close every open WebSocket.

    Called from the FastAPI lifespan shutdown hook. Bounded: every step
    has a small timeout/try-suppress so a misbehaving client can't stall
    process exit.
    """
    global _broadcaster_task

    if _broadcaster_task is not None:
        _broadcaster_task.cancel()
        try:
            await _broadcaster_task
        except (asyncio.CancelledError, Exception):
            pass
        _broadcaster_task = None

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
    ``leaderboard_real.build_live_positions(mongo)`` returns. The list
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


def _build_wire_payload(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Wrap an active-row mutation in the tagged envelope.

    The inner ``row`` schema is a strict subset of ``LivePositionEntry``:
    only the per-tick mutable fields of the active driver. Historicals,
    rank, and best laps come through the ``"snapshot"`` envelope (sent
    on connect and on gate-vectors refresh).

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
    except (TypeError, ValueError):
        return None
    return {"type": "active", "row": row}


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
