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

STATIC_DIR = Path(__file__).parent / "static"


def push_to_clients(value: dict):
    """Called from the Kafka consumer thread; schedules async sends on the main loop."""
    if loop is None or not clients:
        return
    data = json.dumps(value)
    asyncio.run_coroutine_threadsafe(_broadcast(data), loop)


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
    """Run QuixStreams consumer in a background thread."""
    try:
        from quixstreams import Application as QuixApp

        qx = QuixApp(consumer_group="telemetry-dashboard")
        topic_name = os.environ.get("input", "ac-telemetry-raw")
        topic = qx.topic(topic_name)
        sdf = qx.dataframe(topic=topic)
        sdf = sdf.update(push_to_clients)
        logger.info("Starting Kafka consumer on topic '%s'", topic_name)
        qx.run()
    except Exception:
        logger.exception("Kafka consumer failed")


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
api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@api.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@api.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(clients))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(clients))
