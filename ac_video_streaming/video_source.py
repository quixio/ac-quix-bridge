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
import time
from datetime import datetime, timezone

import cv2
from quixstreams.sources import Source

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
        self._fps = int(os.environ.get("VIDEO_FPS", "30"))
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

    @staticmethod
    def _new_session_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

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
        """Upload a finalized MP4 to blob storage, then delete the local file."""
        if not self._blob_fs or not local_path:
            return
        filename = os.path.basename(local_path)
        safe_session = session_id.replace(":", "-")
        blob_path = f"{self._blob_prefix}/session_id={safe_session}/{filename}"
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

    def run(self):
        reader = self._create_reader()
        recorder = VideoRecorder(self._output_dir, self._fps, self._recording_width) if self._recording_enabled else None
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

            # ---- Initialize camera ----
            if camera is None:
                camera, display_size = self._init_camera()
                if camera is None:
                    time.sleep(5)
                    continue

            if next_tick is None:
                next_tick = time.perf_counter()
            next_tick += interval

            # ---- Read AC state ----
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

            # ---- State machine ----

            if status == "live":
                new_session = self._is_new_session(
                    prev_status, status, prev_current_time, current_time
                )

                if new_session:
                    # Finalize any prior recording before starting fresh
                    self._finalize_recording(recorder, "new session", session_id or "")
                    session_id = self._new_session_id()
                    static_data = reader.read_static()
                    logger.info(
                        "New session: %s (%s @ %s)",
                        session_id, static_data["carModel"], static_data["track"],
                    )
                    prev_completed_laps = completed_laps
                    if recorder:
                        recorder.start_lap(session_id, completed_laps, *display_size)

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
                        logger.info("Lap %d recorded: %s", prev_completed_laps, path)
                        self._upload_to_blob(path, session_id)
                        recorder.start_lap(session_id, completed_laps, *display_size)
                    prev_completed_laps = completed_laps

                # Capture frame
                frame = camera.grab()
                if frame is not None:
                    timestamp_ms = int(time.time() * 1000)

                    # Record to MP4
                    if recorder and recorder.is_recording:
                        recorder.write_frame(frame)

                    # Stream to Kafka (throttled to STREAM_FPS)
                    if self._stream_enabled and stream_interval > 0:
                        frame_count += 1
                        if frame_count >= stream_interval:
                            frame_count = 0
                            self._publish_frame(
                                frame, session_id, timestamp_ms, completed_laps
                            )

            elif status == "pause" and prev_status == "live":
                if recorder and recorder.is_recording:
                    recorder.pause()
                logger.info("Recording paused")

            elif status == "off" and prev_status and prev_status != "off":
                self._finalize_recording(recorder, "session ended", session_id or "")
                session_id = None
                prev_completed_laps = None

            prev_status = status
            prev_current_time = current_time

            # ---- Frame rate control ----
            now = time.perf_counter()
            if next_tick > now:
                time.sleep(next_tick - now)

        # ---- Cleanup on source shutdown ----
        self._finalize_recording(recorder, "source stopped", session_id or "")
        if camera is not None:
            del camera
        reader.close()
