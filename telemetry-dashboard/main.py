"""
Telemetry Dashboard — FastAPI + QuixStreams consumer + WebSocket broadcast.

Consumes telemetry from a Kafka topic and pushes it to browser clients
over WebSocket for real-time visualization.
"""

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None
kafka_thread: threading.Thread | None = None

# Consumer health, surfaced to the UI and /health. One of:
# "starting" | "connecting" | "connected" | "reconnecting".
consumer_state: dict[str, str | None] = {"status": "starting", "detail": None}

STATIC_DIR = Path(__file__).parent / "static"


def push_to_clients(value: dict):
    """Called from the Kafka consumer thread; schedules async sends on the main loop."""
    if loop is None or not clients:
        return
    data = json.dumps(value)
    asyncio.run_coroutine_threadsafe(_broadcast(data), loop)


def set_consumer_status(status: str, detail: str | None = None):
    """Update consumer health and push it to connected browsers.

    Called from the Kafka thread, so the broadcast is marshalled onto the loop.
    """
    consumer_state["status"] = status
    consumer_state["detail"] = detail
    logger.info("Consumer status: %s%s", status, f" ({detail})" if detail else "")
    if loop is None or not clients:
        return
    msg = json.dumps({"type": "status", "status": status, "detail": detail})
    asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)


async def _broadcast(data: str):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def run_kafka():
    """Run a raw Kafka consumer in a background thread (no signal handlers needed).

    Wrapped in a reconnect loop so transient failures — a 503 from the Quix
    portal API while building the app, a broker hiccup mid-stream — back off and
    retry instead of killing the thread permanently. Backoff resets on every
    successful connect.
    """
    from quixstreams import Application as QuixApp

    backoff = 1.0
    max_backoff = 30.0

    while True:
        consumer = None
        try:
            set_consumer_status("connecting")

            qx = QuixApp(consumer_group="telemetry-dashboard")
            topic_name = os.environ.get("input", "ac-telemetry-raw")
            topic = qx.topic(topic_name)

            real_topic_name = topic.name
            logger.info("Starting Kafka consumer on topic '%s' (real: '%s')", topic_name, real_topic_name)

            consumer = qx.get_consumer()
            consumer.subscribe([real_topic_name])

            set_consumer_status("connected")
            backoff = 1.0  # connected cleanly — reset backoff

            while True:
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.error("Consumer error: %s", msg.error())
                    continue

                value = json.loads(msg.value())
                push_to_clients(value)

        except Exception:
            logger.exception("Kafka consumer failed — retrying in %.0fs", backoff)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass

        set_consumer_status("reconnecting", f"retry in {int(backoff)}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, kafka_thread
    loop = asyncio.get_event_loop()
    kafka_thread = threading.Thread(target=run_kafka, daemon=True)
    kafka_thread.start()
    logger.info("Dashboard started — open http://localhost:8000")
    yield
    logger.info("Shutting down dashboard")


api = FastAPI(lifespan=lifespan)


@api.get("/health")
async def health():
    """Liveness + consumer health. Registered before the catch-all route."""
    return {
        "status": consumer_state["status"],
        "detail": consumer_state["detail"],
        "clients": len(clients),
    }


@api.get("/{full_path:path}")
async def root(full_path: str = ""):
    index = STATIC_DIR / "index.html"
    logger.info("Request for '/%s' — serving %s (exists: %s)", full_path, index, index.exists())
    if not index.exists():
        # Fallback: try /app/static directly
        fallback = Path("/app/static/index.html")
        logger.info("Trying fallback %s (exists: %s)", fallback, fallback.exists())
        if fallback.exists():
            return FileResponse(str(fallback))
        # List what's actually in /app for debugging
        app_contents = list(Path("/app").rglob("*"))
        logger.info("Contents of /app: %s", app_contents)
        return {"error": "index.html not found", "static_dir": str(STATIC_DIR), "app_contents": [str(p) for p in app_contents]}
    return FileResponse(str(index))


@api.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(clients))
    # Send current consumer health immediately so a freshly-loaded page reflects
    # reality instead of assuming "live" just because the socket opened.
    await websocket.send_text(
        json.dumps({"type": "status", "status": consumer_state["status"], "detail": consumer_state["detail"]})
    )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(clients))
