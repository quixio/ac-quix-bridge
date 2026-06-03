"""
Video Stream Viewer — FastAPI + Kafka consumer + WebSocket broadcast.

Consumes JPEG frames from the ac-video-frames Kafka topic and pushes
them to browser clients over WebSocket for live video display.

Run alongside main.py:
  Terminal 1: python main.py       (captures + produces)
  Terminal 2: python viewer.py     (consumes + serves webpage)
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

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None

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
    """Consume video frames from Kafka in a background thread."""
    try:
        from quixstreams import Application as QuixApp

        qx = QuixApp(consumer_group="video-viewer")
        topic_name = os.environ.get("input", "ac-video-frames")
        topic = qx.topic(topic_name)

        real_topic_name = topic.name
        logger.info("Starting Kafka consumer on topic '%s' (real: '%s')", topic_name, real_topic_name)

        consumer = qx.get_consumer()
        consumer.subscribe([real_topic_name])

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
        logger.exception("Kafka consumer failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_event_loop()
    kafka_thread = threading.Thread(target=run_kafka, daemon=True)
    kafka_thread.start()
    logger.info("Video viewer started — open http://localhost:%s", os.environ.get("VIEWER_PORT", "8080"))
    yield
    logger.info("Shutting down viewer")


api = FastAPI(lifespan=lifespan)


@api.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    logger.info("Viewer connected (%d total)", len(clients))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
        logger.info("Viewer disconnected (%d remaining)", len(clients))


api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@api.get("/{full_path:path}")
async def root(full_path: str = ""):
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("VIEWER_PORT", "8080"))
    uvicorn.run(api, host="0.0.0.0", port=port)
