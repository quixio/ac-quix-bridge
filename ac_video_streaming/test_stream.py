"""
Standalone live streaming test — no Kafka needed.

Captures your screen using dxcam with mock AC session lifecycle and streams
JPEG frames directly to a browser via FastAPI + WebSocket.

Run:
  .venv\\Scripts\\python test_stream.py

Then open: http://localhost:8080
"""

import asyncio
import base64
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import cv2

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from ac_reader_mock import ACGraphicsReaderMock
from video_recorder import VideoRecorder

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

# --- Shared state ---
clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None

STATIC_DIR = Path(__file__).parent / "static"


def push_to_clients(data: dict):
    if loop is None or not clients:
        return
    payload = json.dumps(data)
    asyncio.run_coroutine_threadsafe(_broadcast(payload), loop)


async def _broadcast(payload: str):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


# --- Capture + record + stream thread ---

def capture_loop():
    fps = int(os.environ.get("VIDEO_FPS", "30"))
    stream_fps = int(os.environ.get("STREAM_FPS", "15"))
    display_index = int(os.environ.get("VIDEO_DISPLAY_INDEX", "0"))
    output_dir = os.environ.get("VIDEO_OUTPUT_DIR", "./recordings")
    recording_width = int(os.environ.get("RECORDING_WIDTH", "1920"))
    stream_width = int(os.environ.get("STREAM_WIDTH", "1280"))
    jpeg_quality = int(os.environ.get("JPEG_QUALITY", "75"))
    recording_enabled = os.environ.get("VIDEO_RECORDING_ENABLED", "true").lower() == "true"

    import dxcam
    logger.info("Initializing screen capture on display %d...", display_index)
    camera = dxcam.create(output_idx=display_index)
    frame = camera.grab()
    if frame is None:
        logger.error("Failed to grab frame from display %d", display_index)
        return
    h, w = frame.shape[:2]
    logger.info("Screen capture ready: %dx%d → recording %dp, streaming %dp",
                w, h, recording_width, stream_width)

    recorder = VideoRecorder(output_dir, fps, recording_width) if recording_enabled else None
    reader = ACGraphicsReaderMock()
    reader.open()

    prev_status = None
    prev_completed_laps = None
    prev_current_time = None
    session_id = None
    frame_count = 0
    stream_interval = max(1, fps // stream_fps) if stream_fps > 0 else 1

    interval = 1.0 / fps
    next_tick = time.perf_counter()

    while True:
        next_tick += interval
        gfx = reader.read_graphics()
        status = gfx["status"]
        completed_laps = gfx["completedLaps"]
        current_time = gfx["iCurrentTime"]

        if status == "live":
            # New session?
            new_session = False
            if prev_status != "live":
                if prev_status is None or prev_status in ("off", "replay"):
                    new_session = True
                elif prev_status == "pause":
                    if prev_current_time is not None and current_time < prev_current_time:
                        new_session = True
                    else:
                        if recorder and recorder.is_recording:
                            recorder.resume()
                        logger.info(">> RESUMED")

            if new_session:
                if recorder and recorder.is_recording:
                    recorder.finish_lap()
                session_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                static = reader.read_static()
                logger.info(">> NEW SESSION: %s (%s @ %s)", session_id, static["carModel"], static["track"])
                prev_completed_laps = completed_laps
                if recorder:
                    recorder.start_lap(session_id, completed_laps, w, h)

            # Lap change
            if not new_session and prev_completed_laps is not None and completed_laps > prev_completed_laps:
                if recorder and recorder.is_recording:
                    recorder.finish_lap()
                logger.info(">> LAP %d COMPLETE", prev_completed_laps)
                if recorder:
                    recorder.start_lap(session_id, completed_laps, w, h)
                prev_completed_laps = completed_laps

            # Capture
            frame = camera.grab()
            if frame is not None:
                timestamp_ms = int(time.time() * 1000)

                # Record
                if recorder and recorder.is_recording:
                    recorder.write_frame(frame)

                # Stream to WebSocket clients
                frame_count += 1
                if frame_count >= stream_interval:
                    frame_count = 0
                    # Resize for streaming
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    sh, sw = frame_bgr.shape[:2]
                    if sw > stream_width:
                        scale = stream_width / sw
                        frame_bgr = cv2.resize(frame_bgr, (stream_width, int(sh * scale)))
                    _, jpeg_buf = cv2.imencode(".jpg", frame_bgr,
                                               [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
                    frame_b64 = base64.b64encode(jpeg_buf.tobytes()).decode("ascii")

                    push_to_clients({
                        "session_id": session_id,
                        "timestamp_ms": timestamp_ms,
                        "completedLaps": completed_laps,
                        "frame": frame_b64,
                    })

        elif status == "pause" and prev_status == "live":
            if recorder and recorder.is_recording:
                recorder.pause()
            logger.info(">> PAUSED")

        elif status == "off" and prev_status and prev_status != "off":
            if recorder and recorder.is_recording:
                recorder.finish_lap()
            logger.info(">> SESSION ENDED")
            session_id = None
            prev_completed_laps = None

        prev_status = status
        prev_current_time = current_time

        now = time.perf_counter()
        if next_tick > now:
            time.sleep(next_tick - now)


# --- FastAPI app ---

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop
    loop = asyncio.get_event_loop()
    capture_thread = threading.Thread(target=capture_loop, daemon=True)
    capture_thread.start()
    port = os.environ.get("VIEWER_PORT", "8080")
    logger.info("")
    logger.info("==============================================")
    logger.info("  Open in browser: http://localhost:%s", port)
    logger.info("==============================================")
    logger.info("")
    yield

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
