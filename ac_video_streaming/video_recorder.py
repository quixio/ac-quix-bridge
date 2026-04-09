"""
Per-lap MP4 recording via ffmpeg subprocess.

Each lap (or partial lap) is saved as a separate MP4 file with the session_id
and lap number in the filename. Recording can be paused/resumed to skip
frames while the game is paused.

Frames are resized to RECORDING_WIDTH (default 1920) before encoding to keep
ffmpeg encoding in real time even at 4K capture resolution.
"""

import logging
import os
import shutil
import subprocess

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _find_ffmpeg() -> str:
    """Find ffmpeg binary — system PATH first, then static_ffmpeg fallback."""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
        path = shutil.which("ffmpeg")
        if path:
            logger.info("Using static_ffmpeg: %s", path)
            return path
    except ImportError:
        pass
    return "ffmpeg"


class VideoRecorder:
    """Manages per-lap MP4 recording lifecycle via ffmpeg."""

    def __init__(self, output_dir: str, fps: int, max_width: int = 1920):
        self._output_dir = output_dir
        self._fps = fps
        self._max_width = max_width
        self._ffmpeg = _find_ffmpeg()
        self._process: subprocess.Popen | None = None
        self._current_path: str = ""
        self._paused = False
        self._rec_w: int = 0
        self._rec_h: int = 0
        os.makedirs(output_dir, exist_ok=True)

    @property
    def is_recording(self) -> bool:
        return self._process is not None

    def _calc_recording_size(self, src_w: int, src_h: int) -> tuple[int, int]:
        """Scale down to max_width if source is larger, keeping aspect ratio.
        Width and height are rounded to even numbers (required by libx264)."""
        if src_w <= self._max_width:
            w, h = src_w, src_h
        else:
            scale = self._max_width / src_w
            w = self._max_width
            h = int(src_h * scale)
        # libx264 requires even dimensions
        return w - (w % 2), h - (h % 2)

    def start_lap(self, session_id: str, lap: int, width: int, height: int) -> str:
        """Start recording a new lap. Returns the output filepath."""
        if self._process is not None:
            self.finish_lap()

        self._rec_w, self._rec_h = self._calc_recording_size(width, height)

        safe_id = session_id.replace(":", "-")
        filename = f"{safe_id}_lap{lap:03d}.mp4"
        filepath = os.path.join(self._output_dir, filename)

        try:
            self._process = subprocess.Popen(
                [
                    self._ffmpeg, "-y",
                    "-f", "rawvideo",
                    "-pix_fmt", "rgb24",
                    "-s", f"{self._rec_w}x{self._rec_h}",
                    "-r", str(self._fps),
                    "-i", "-",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-crf", "23",
                    "-pix_fmt", "yuv420p",
                    filepath,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_path = filepath
            self._paused = False
            logger.info(
                "Recording started: %s (%dx%d @ %dfps)",
                filename, self._rec_w, self._rec_h, self._fps,
            )
            return filepath
        except FileNotFoundError:
            logger.error(
                "ffmpeg not found. Install it: winget install ffmpeg "
                "or download from https://ffmpeg.org/download.html"
            )
            self._process = None
            return ""

    def write_frame(self, frame: np.ndarray):
        """Write a frame (numpy array, RGB) to the current recording.
        Automatically resizes to the recording resolution if needed."""
        if self._process is None or self._paused:
            return
        h, w = frame.shape[:2]
        if w != self._rec_w or h != self._rec_h:
            frame = cv2.resize(frame, (self._rec_w, self._rec_h))
        try:
            self._process.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            logger.error("ffmpeg pipe broken, stopping recording")
            self._cleanup_process()

    def pause(self):
        """Pause recording — frames are skipped until resume()."""
        self._paused = True

    def resume(self):
        """Resume recording after a pause."""
        self._paused = False

    def finish_lap(self) -> str:
        """Finalize the current MP4 and return its filepath."""
        if self._process is None:
            return ""
        path = self._current_path
        try:
            self._process.stdin.close()
            self._process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg did not finish in time, killing")
            self._process.kill()
        except Exception:
            logger.exception("Error finalizing recording")
            self._process.kill()
        self._cleanup_process()
        if path:
            logger.info("Recording finalized: %s", path)
        return path

    def _cleanup_process(self):
        self._process = None
        self._current_path = ""
        self._paused = False
