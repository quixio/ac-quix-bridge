import asyncio
import os
import time
import threading
from collections import deque

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from quixstreams import Application

# ── Config ────────────────────────────────────────────────────────────────────
INPUT_TOPIC = os.environ["input"]
WINDOW_SECONDS = int(os.environ.get("WINDOW_SECONDS", "30"))
MAX_POINTS = WINDOW_SECONDS * 60  # assume up to 60 Hz

# ── Shared state ──────────────────────────────────────────────────────────────
buffer: deque = deque(maxlen=MAX_POINTS)
subscribers: list[asyncio.Queue] = []
subscribers_lock = threading.Lock()
main_loop: asyncio.AbstractEventLoop | None = None  # set before thread starts

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI()

with open(os.path.join(os.path.dirname(__file__), "static", "index.html")) as f:
    INDEX_HTML = f.read()


@app.get("/")
async def index():
    return HTMLResponse(INDEX_HTML)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    queue: asyncio.Queue = asyncio.Queue()
    with subscribers_lock:
        subscribers.append(queue)
    try:
        snapshot = list(buffer)
        await websocket.send_json({"type": "snapshot", "data": snapshot})
        while True:
            point = await queue.get()
            await websocket.send_json({"type": "point", "data": point})
    except WebSocketDisconnect:
        pass
    finally:
        with subscribers_lock:
            subscribers.remove(queue)


# ── Quix Streams consumer (runs in background thread) ─────────────────────────
def run_consumer():
    quix_app = Application(
        consumer_group="speed-dashboard",
        auto_offset_reset="latest",
    )
    topic = quix_app.topic(INPUT_TOPIC, value_deserializer="json")
    sdf = quix_app.dataframe(topic)

    def process(row: dict):
        ts = row.get("timestamp_ms") or row.get("Timestamp") or (time.time() * 1000)
        speed = row.get("speedKmh") or row.get("SpeedKmh")
        if speed is None:
            return
        point = {"t": float(ts), "v": float(speed)}
        buffer.append(point)
        if main_loop is not None:
            with subscribers_lock:
                for q in list(subscribers):
                    main_loop.call_soon_threadsafe(q.put_nowait, point)

    sdf = sdf.update(process)
    quix_app._setup_signal_handlers = lambda: None
    quix_app.run()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    async def start():
        global main_loop
        main_loop = asyncio.get_event_loop()
        t = threading.Thread(target=run_consumer, daemon=True)
        t.start()
        config = uvicorn.Config(app, host="0.0.0.0", port=80)
        server = uvicorn.Server(config)
        await server.serve()

    asyncio.run(start())
