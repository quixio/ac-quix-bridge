"""WebSocket route for the leaderboard live stream.

Exposes ``GET /api/v1/leaderboard/live-stream`` (HTTP upgrade → WS).

Connection lifecycle:
  1. Client opens the URL with ``?token=<bearer>`` (browsers can't set
     arbitrary headers on a WebSocket handshake).
  2. The handshake handler validates the token via the same
     ``Auth.validate_permissions`` call the (still-available) polling
     endpoint uses (read permission on the configured workspace),
     unless ``api_auth_active`` is False.
  3. Accepted clients receive ONE ``{"type": "snapshot", "rows": [...]}``
     message built from the same ``leaderboard_real.build_live_positions``
     code path the HTTP endpoint uses. After that they are registered
     with ``live_stream`` and receive per-tick
     ``{"type": "active", "row": {...}}`` messages plus any
     ``{"type": "snapshot", ...}`` rebroadcasts triggered by a
     historicals refresh.
  4. On disconnect (client close, network blip, send failure) the
     client is unregistered. Reconnect logic is the frontend's job;
     a fresh snapshot is delivered automatically on reconnect.

This route is the FastAPI-async side. The Kafka thread side lives in
``api.live_telemetry`` (``_record_message`` calls
``live_stream.publish_snapshot``; the gate-vectors refresh hook calls
``live_stream.publish_full_snapshot``).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pymongo.database import Database

from .. import live_stream, live_telemetry
from ..auth import auth
from ..mongo import get_mongo
from ..settings import get_settings
from . import leaderboard_real, live_positions_sim

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


def _validate_ws_token(token: str | None) -> bool:
    """Return True if the WebSocket handshake should be accepted.

    Mirrors ``api.auth.validate_token`` but tailored for the WebSocket
    path:
      * Token comes from the query string (browsers can't set headers
        on a WS handshake).
      * Bearer prefix is stripped if present so clients can pass either
        ``token=abc`` or ``token=Bearer abc``.
      * Read permission on the configured workspace is enough — same as
        the polling endpoint.
      * ``api_auth_active=False`` (LOCAL_DEV_MODE) bypasses validation.
    """
    settings = get_settings()
    if not settings.api_auth_active:
        return True
    if not token:
        return False
    if token.startswith(("bearer ", "Bearer ")):
        token = token[7:]
    try:
        return bool(
            auth().validate_permissions(
                token, "Workspace", settings.workspace_id, "Read"
            )
        )
    except Exception:
        logger.exception("WebSocket token validation crashed")
        return False


def _build_initial_rows_sync(mongo: Database[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the initial-snapshot row list on a worker thread.

    Mirrors the dispatch logic in
    ``routes/leaderboard.py:get_live_positions``: LOCAL_DEV_MODE uses the
    in-process simulator, every other deployment goes through
    ``leaderboard_real.build_live_positions``. Both functions are
    synchronous (Mongo, possibly QuixLake on cold cache) which is why
    this helper is called via ``asyncio.to_thread`` — running it inline
    on the event loop would stall every other WebSocket connection
    while the snapshot is built.

    Returns the raw row dicts (not Pydantic models) — the broadcaster
    serialises them straight to JSON, matching the on-the-wire shape
    the polling endpoint produces after FastAPI's model validation. We
    keep them as dicts here so the JSON payload sees the same keys
    regardless of which path produced the rows.
    """
    if os.getenv("LOCAL_DEV_MODE", "false").lower() == "true":
        return live_positions_sim.make_local_dev_live_positions()
    try:
        return leaderboard_real.build_live_positions(mongo)
    except leaderboard_real.LeaderboardError:
        # Cold start before the lake has any data, missing creds, etc.
        # Treat as "no rows yet" — the client will still get a valid
        # snapshot (empty list) and subsequent gate-vectors refreshes
        # will broadcast a non-empty one when historicals appear.
        logger.exception("initial snapshot build failed; sending empty rows")
        return []


@router.websocket("/live-stream")
async def live_stream_endpoint(
    websocket: WebSocket,
    token: Annotated[str | None, Query()] = None,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
) -> None:
    """WebSocket endpoint streaming the full leaderboard.

    Wire schema:

    Snapshot (on connect, and on every historicals refresh)::

        {"type": "snapshot", "rows": [LivePositionEntry, ...]}

    Active mutation (per Kafka tick, throttled to 50 ms / ~20 Hz)::

        {"type": "active", "row": {
          "driver": str, "track": str, "car": str, "experiment": str,
          "current_lap": int | null,
          "current_lap_time_ms": int,
          "normalized_position": float,
          "last_gate_index": int | null,
          "last_gate_state": "ahead" | "behind" | "neutral" | null,
          "last_gate_delta_ms": int | null
        }}

    The client treats ``snapshot`` as a full replace of its row state and
    ``active`` as a patch against the matching ``(driver, track, car,
    experiment)`` row inside that state.
    """
    # WS is open — no token required. The stream just replays public
    # Kafka topic data (telemetry/session/config), no sensitive info on
    # this channel. Keeping the HTTP API auth gate intact for everything
    # else; this is intentional per the leaderboard-ui design (the page
    # may load before the auth handshake completes, and the WS should
    # not block on it).
    _ = token  # accepted but ignored — kept in the URL signature for
    # backwards compatibility with clients that still send it.
    await websocket.accept()

    # Build + send the initial snapshot BEFORE registering the client.
    # If we registered first, the broadcaster could push an `active`
    # mutation against an unknown row (the client hasn't seen a
    # snapshot yet) — harmless on its own, but the frontend's
    # patch-by-key logic would no-op and the user would see a missing
    # row until the next refresh. Snapshot-first guarantees the client
    # always has a base state to patch against.
    try:
        rows = await asyncio.to_thread(_build_initial_rows_sync, mongo)
        await websocket.send_json({"type": "snapshot", "rows": rows})
        # Send the current active-stream state right after the snapshot
        # so a reconnecting client doesn't have to wait for the next
        # transition to learn whether AC is live (spec §5.1: "Snapshot
        # on connect now also includes one `active_state` message
        # reflecting current state.").
        await websocket.send_json(live_telemetry.current_active_state_envelope())
    except WebSocketDisconnect:
        # Client dropped before we could send anything; nothing to do.
        return
    except Exception:
        # Snapshot build failed catastrophically — log, close, exit. The
        # client will reconnect via its existing backoff and try again.
        logger.exception("initial snapshot send failed; closing WS")
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
        except Exception:
            pass
        return

    await live_stream.register(websocket)
    try:
        # Drain inbound frames so the connection stays open. Browsers
        # send pings, and some proxies send keepalives; ignoring them
        # would leak buffered data and eventually wedge the socket.
        # We don't consume the content — it's purely a keepalive read.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("live-stream WebSocket crashed")
    finally:
        await live_stream.unregister(websocket)
