"""
QuixStreams Source that captures Assetto Corsa gameplay video.

- Reads AC shared memory (graphics/static) for session and status detection
- Captures the game display via dxcam (DirectX Desktop Duplication)
- Records per-lap MP4 files locally via ffmpeg, then uploads to blob storage
- Publishes JPEG frames to a Kafka topic for live streaming via Quix Cloud

State machine (mirrors ac-telemetry-source/ac_source.py):
  off/None → live:   new session → start recording
  pause → live:      resume (or new session if iCurrentTime dropped)
  live → pause:      pause recording
  live → off:        finalize MP4, upload to blob storage
  shm lost:          finalize MP4, upload to blob storage, reconnect
  lap change:        finalize current lap MP4, upload, start next lap
"""

import base64
import logging
import os
import threading
import time
from datetime import datetime, timezone

import cv2
from quixstreams.sources import Source

from session_tracker import SessionTracker
from video_recorder import VideoRecorder

logger = logging.getLogger(__name__)


def _get_blob_fs():
    """Get quixportal filesystem for blob storage. Returns None if unavailable."""
    try:
        from quixportal.storage import get_filesystem
        fs = get_filesystem()
        logger.info("Blob storage connected")
        return fs
    except Exception as e:
        logger.warning("Blob storage not available, MP4s will remain local only: %s", e)
        return None


class ACVideoSource(Source):
    """Captures AC gameplay, records per-lap MP4s, and streams frames to Kafka."""

    def __init__(self, name: str):
        super().__init__(name=name)
        self._display_index = int(os.environ.get("VIDEO_DISPLAY_INDEX", "0"))
        self._fps = int(os.environ.get("VIDEO_FPS", "15"))
        self._stream_fps = int(os.environ.get("STREAM_FPS", "15"))
        self._output_dir = os.environ.get("VIDEO_OUTPUT_DIR", "./recordings")
        self._blob_prefix = os.environ.get("BLOB_VIDEO_PREFIX", "ac_video")
        self._recording_enabled = os.environ.get("VIDEO_RECORDING_ENABLED", "true").lower() == "true"
        self._stream_enabled = os.environ.get("VIDEO_STREAM_ENABLED", "true").lower() == "true"
        self._stream_width = int(os.environ.get("STREAM_WIDTH", "1280"))
        self._jpeg_quality = int(os.environ.get("JPEG_QUALITY", "75"))
        self._recording_width = int(os.environ.get("RECORDING_WIDTH", "1920"))
        self._mock_mode = os.environ.get("AC_MOCK_MODE", "false").lower() == "true"
        self._blob_fs = _get_blob_fs() if self._recording_enabled else None
        # SessionTracker is created in run() (the child subprocess) — it holds
        # a threading.Lock which can't be pickled across processes.
        self._session_tracker: SessionTracker | None = None

    @staticmethod
    def _fallback_session_id() -> str:
        """Used only when the telemetry session topic is unreachable. Video
        recorded with this id won't be syncable in the Telemetry Explorer."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _resolve_session_id(self) -> str:
        """Adopt the telemetry-published session_id, or fall back if unavailable.

        Waits up to 15s — empirically the tracker's mini-Application takes
        ~5-7s to fetch broker config from the Quix portal API on first init,
        and another 1-2s for partition assignment + first poll. A short
        timeout here causes the fallback path to fire even when telemetry is
        healthy."""
        detect_ms = int(time.time() * 1000)
        if self._session_tracker is not None:
            sid = self._session_tracker.session_id_for_new_session(
                our_detect_ms=detect_ms, timeout_s=15.0
            )
            if sid is not None:
                return sid
            logger.warning(
                "No telemetry session_id received within 15s — "
                "telemetry source may not be running. Falling back to local id; "
                "this video will not be syncable in the Telemetry Explorer."
            )
        return self._fallback_session_id()

    def _init_camera(self):
        """Initialize dxcam screen capture. Returns (camera, (width, height)) or (None, None)."""
        try:
            import dxcam
        except ImportError:
            logger.error(
                "dxcam is not installed. Install it: pip install dxcam"
            )
            return None, None

        try:
            camera = dxcam.create(output_idx=self._display_index)
            frame = camera.grab()
            if frame is None:
                logger.error(
                    "Failed to grab initial frame from display %d. "
                    "Is the display active and not in an RDP session?",
                    self._display_index,
                )
                return None, None
            h, w = frame.shape[:2]
            logger.info(
                "Camera initialized: display %d, resolution %dx%d",
                self._display_index, w, h,
            )
            return camera, (w, h)
        except Exception:
            logger.exception("Failed to initialize dxcam on display %d", self._display_index)
            return None, None

    def _is_new_session(self, prev_status, status, prev_current_time, current_time) -> bool:
        """Determine if a status transition means a new session started."""
        if prev_status == "live":
            return False
        if prev_status is None or prev_status in ("off", "replay"):
            return True
        if prev_status == "pause":
            return prev_current_time is not None and current_time < prev_current_time
        return True

    def _upload_to_blob(self, local_path: str, session_id: str):
        """Upload a finalized MP4 + its sidecar JSON to blob storage, then
        delete the local files. Sidecar is best-effort: a missing sidecar
        does not block the MP4 upload."""
        if not self._blob_fs or not local_path:
            return
        safe_session = session_id.replace(":", "-")
        folder = f"{self._blob_prefix}/session_id={safe_session}"

        self._upload_one(local_path, f"{folder}/{os.path.basename(local_path)}")

        sidecar_path = VideoRecorder.sidecar_path_for(local_path)
        if os.path.exists(sidecar_path):
            self._upload_one(sidecar_path, f"{folder}/{os.path.basename(sidecar_path)}")
        else:
            logger.warning(
                "No sidecar found for %s — Telemetry Explorer sync unavailable for this lap",
                os.path.basename(local_path),
            )

    def _upload_one(self, local_path: str, blob_path: str):
        """Upload one local file to blob_path, then delete it locally."""
        filename = os.path.basename(local_path)
        try:
            with open(local_path, "rb") as f:
                self._blob_fs.pipe(blob_path, f.read())
            logger.info("Uploaded to blob storage: %s", blob_path)
            os.remove(local_path)
            logger.info("Deleted local file: %s", filename)
        except Exception:
            logger.exception("Failed to upload %s to blob storage (local file kept)", filename)

    def _finalize_recording(self, recorder: VideoRecorder | None, reason: str, session_id: str = ""):
        if recorder and recorder.is_recording:
            path = recorder.finish_lap()
            logger.info("Recording finalized (%s): %s", reason, path)
            self._upload_to_blob(path, session_id)

    def _publish_frame(self, frame, session_id: str, timestamp_ms: int, completed_laps: int):
        """Encode frame as JPEG and publish to Kafka topic."""
        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

        # Resize for streaming
        h, w = frame_bgr.shape[:2]
        if w > self._stream_width:
            scale = self._stream_width / w
            new_h = int(h * scale)
            frame_bgr = cv2.resize(frame_bgr, (self._stream_width, new_h))

        _, jpeg_buf = cv2.imencode(
            ".jpg", frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
        )
        frame_b64 = base64.b64encode(jpeg_buf.tobytes()).decode("ascii")

        msg = self.serialize(
            key=session_id,
            value={
                "session_id": session_id,
                "timestamp_ms": timestamp_ms,
                "completedLaps": completed_laps,
                "frame": frame_b64,
            },
        )
        self.produce(key=msg.key, value=msg.value)

    def _create_reader(self):
        if self._mock_mode:
            from ac_reader_mock import ACGraphicsReaderMock
            logger.info("Using MOCK AC reader (AC_MOCK_MODE=true)")
            return ACGraphicsReaderMock()
        from ac_reader import ACGraphicsReader
        return ACGraphicsReader()

    def _start_session_tracker_thread(self):
        """Spawn a background thread in the Source's subprocess that consumes
        the ac-telemetry-session topic and feeds self._session_tracker.

        Runs in the child process so that the SessionTracker (with its Lock)
        never has to be pickled across the process boundary. Uses a fresh
        QuixStreams Application just to obtain a Kafka Consumer with broker
        config auto-resolved from Quix__Sdk__Token."""
        import json

        self._session_tracker = SessionTracker()
        session_topic_name = os.environ.get("session_input", "ac-telemetry-session")
        tracker = self._session_tracker

        def _run():
            consumer = None
            try:
                from quixstreams import Application as _App
                mini_app = _App(
                    consumer_group=(
                        f"ac_video_session_tracker_{os.getpid()}_{int(time.time() * 1000)}"
                    ),
                    auto_offset_reset="earliest",
                    auto_create_topics=False,
                )
                # Use Application.topic() so the resolved name is workspace-
                # prefixed (e.g. "quixers-acquixbridge-videostreaming-ac-telemetry-session").
                # consumer.subscribe() takes raw Kafka topic names with no
                # auto-prefixing, so the bare name silently subscribes to a
                # topic that doesn't exist.
                session_topic = mini_app.topic(name=session_topic_name)
                resolved_name = session_topic.name
                consumer = mini_app.get_consumer(auto_commit_enable=False)
                consumer.subscribe([resolved_name])
                logger.info(
                    "Session tracker subscribed to %s (resolved from %s)",
                    resolved_name, session_topic_name,
                )
            except Exception:
                logger.exception(
                    "Session tracker setup failed — video will use fallback "
                    "session_id and won't be syncable in Telemetry Explorer"
                )
                return

            while self.running:
                try:
                    msg = consumer.poll(1.0)
                    if msg is None:
                        continue
                    if msg.error():
                        logger.warning("Session topic consumer error: %s", msg.error())
                        continue
                    raw = msg.value()
                    if raw is None:
                        continue
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", errors="replace")
                    data = json.loads(raw) if isinstance(raw, str) else raw
                    tracker.update_from_message(data)
                except Exception:
                    logger.exception("Session topic poll error")
                    time.sleep(0.5)

            try:
                consumer.close()
            except Exception:
                pass

        t = threading.Thread(target=_run, daemon=True, name="session-tracker")
        t.start()

    def run(self):
        self._start_session_tracker_thread()
        reader = self._create_reader()
        sidecar_hz = float(os.environ.get("SIDECAR_SAMPLE_HZ", "5"))
        recorder = (
            VideoRecorder(
                self._output_dir, self._fps, self._recording_width,
                sidecar_sample_hz=sidecar_hz,
            )
            if self._recording_enabled
            else None
        )
        camera = None
        display_size = None

        prev_status = None
        prev_completed_laps = None
        prev_current_time = None
        session_id = None

        frame_count = 0
        stream_interval = max(1, self._fps // self._stream_fps) if self._stream_fps > 0 else 0

        interval = 1.0 / self._fps
        next_tick = None

        # Background thread for Kafka streaming (decoupled from capture loop)
        stream_frame_lock = threading.Lock()
        stream_frame_data = {"frame": None, "session_id": "", "timestamp_ms": 0, "laps": 0}
        stream_running = True

        def _stream_thread():
            """Publishes frames to Kafka in a background thread so it doesn't slow capture."""
            last_frame_id = None
            while stream_running and self.running:
                with stream_frame_lock:
                    frame = stream_frame_data["frame"]
                    sid = stream_frame_data["session_id"]
                    ts = stream_frame_data["timestamp_ms"]
                    laps = stream_frame_data["laps"]
                if frame is not None and id(frame) != last_frame_id:
                    last_frame_id = id(frame)
                    self._publish_frame(frame, sid, ts, laps)
                else:
                    time.sleep(0.01)

        if self._stream_enabled:
            streamer = threading.Thread(target=_stream_thread, daemon=True)
            streamer.start()

        while self.running:
            # ---- Connect to AC shared memory ----
            if not reader.is_open:
                try:
                    reader.open()
                except FileNotFoundError:
                    logger.warning(
                        "AC shared memory not available — is Assetto Corsa running? "
                        "Retrying in 5 seconds..."
                    )
                    time.sleep(5)
                    next_tick = None
                    continue

            # ---- Read AC state (before camera init to avoid blocking game) ----
            try:
                gfx = reader.read_graphics()
            except Exception:
                logger.exception("Shared memory read error, reconnecting...")
                reader.close()
                self._finalize_recording(recorder, "AC disconnected", session_id or "")
                session_id = None
                prev_status = None
                prev_completed_laps = None
                prev_current_time = None
                next_tick = None
                time.sleep(5)
                continue

            status = gfx["status"]
            completed_laps = gfx["completedLaps"]
            current_time = gfx["iCurrentTime"]
            in_pit = gfx.get("isInPit", False) or gfx.get("isInPitLane", False)

            # ---- Initialize camera only when LIVE and out of pit ----
            if camera is None and status == "live" and not in_pit:
                logger.info("AC is LIVE — initializing screen capture...")
                camera, display_size = self._init_camera()
                if camera is None:
                    prev_status = status
                    prev_current_time = current_time
                    time.sleep(5)
                    continue

            # Wait for camera + LIVE + out of pit before entering main loop
            if camera is None or status != "live" or in_pit:
                if status == "off" and prev_status and prev_status != "off":
                    self._finalize_recording(recorder, "session ended", session_id or "")
                    session_id = None
                    prev_completed_laps = None
                elif status == "pause" and prev_status == "live":
                    if recorder and recorder.is_recording:
                        recorder.pause()
                    logger.info("Recording paused")
                elif in_pit and recorder and recorder.is_recording:
                    recorder.pause()
                    logger.info("In pit — recording paused")
                prev_status = status
                prev_current_time = current_time
                time.sleep(0.1)
                continue

            if next_tick is None:
                next_tick = time.perf_counter()
            next_tick += interval

            # ---- State machine (status is LIVE here) ----

            new_session = self._is_new_session(
                prev_status, status, prev_current_time, current_time
            )

            if new_session:
                # Finalize any prior recording before starting fresh
                self._finalize_recording(recorder, "new session", session_id or "")
                session_id = self._resolve_session_id()
                static_data = reader.read_static()
                logger.info(
                    "New session: %s (%s @ %s)",
                    session_id, static_data["carModel"], static_data["track"],
                )
                prev_completed_laps = completed_laps
                if recorder:
                    # Lake sink stores lap = completedLaps + 1 (out-lap = "lap 1
                    # in progress"). Use the same convention here so MP4/sidecar
                    # filenames align with telemetry lap numbers in the Explorer.
                    recorder.start_lap(session_id, completed_laps + 1, *display_size)

            elif prev_status == "pause":
                # Resume from pause
                if recorder and recorder.is_recording:
                    recorder.resume()
                logger.info("Recording resumed")

            # Lap change detection
            if (
                not new_session
                and prev_completed_laps is not None
                and completed_laps > prev_completed_laps
            ):
                if recorder and recorder.is_recording:
                    path = recorder.finish_lap()
                    logger.info("Lap %d recorded: %s", prev_completed_laps + 1, path)
                    self._upload_to_blob(path, session_id)
                    # Same +1 convention as new-session start_lap above.
                    recorder.start_lap(session_id, completed_laps + 1, *display_size)
                prev_completed_laps = completed_laps

            # Capture frame
            frame = camera.grab()
            if frame is not None:
                timestamp_ms = int(time.time() * 1000)

                # Record to MP4 (fast — just writes raw bytes to ffmpeg pipe)
                if recorder and recorder.is_recording:
                    recorder.write_frame(frame)
                    recorder.log_frame(timestamp_ms, gfx.get("normalizedCarPosition"))

                # Hand frame to stream thread (non-blocking)
                if self._stream_enabled and stream_interval > 0:
                    frame_count += 1
                    if frame_count >= stream_interval:
                        frame_count = 0
                        with stream_frame_lock:
                            stream_frame_data["frame"] = frame
                            stream_frame_data["session_id"] = session_id
                            stream_frame_data["timestamp_ms"] = timestamp_ms
                            stream_frame_data["laps"] = completed_laps

            prev_status = status
            prev_current_time = current_time

            # ---- Frame rate control ----
            now = time.perf_counter()
            if next_tick > now:
                time.sleep(next_tick - now)

        # ---- Cleanup on source shutdown ----
        stream_running = False
        self._finalize_recording(recorder, "source stopped", session_id or "")
        if camera is not None:
            del camera
        reader.close()
