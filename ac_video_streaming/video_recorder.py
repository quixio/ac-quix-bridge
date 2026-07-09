"""
Per-lap MP4 recording via ffmpeg subprocess.

Each lap (or partial lap) is saved as a separate MP4 file with the session_id
and lap number in the filename. Recording can be paused/resumed to skip
frames while the game is paused.

Frames are resized to RECORDING_WIDTH (default 1920) before encoding to keep
ffmpeg encoding in real time even at 4K capture resolution.

Each finished MP4 is paired with a sidecar JSON (<mp4>.sync.json) that maps
sub-sampled frame indices to wall-clock time and AC's normalizedCarPosition.
The Telemetry Explorer uses the sidecar to bind plot-marker drag <-> video
seek (see docs/video-sync-design.md).
"""

import json
import logging
import os
import shutil
import subprocess
import time

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
    """Manages per-lap MP4 recording lifecycle via ffmpeg.

    Also tracks per-frame wall-clock + AC position metadata at sidecar_sample_hz
    and emits a `<mp4>.sync.json` sidecar on finish_lap() for video <-> telemetry
    sync in the Telemetry Explorer.
    """

    def __init__(
        self,
        output_dir: str,
        fps: int,
        max_width: int = 1920,
        sidecar_sample_hz: float = 5.0,
    ):
        self._output_dir = output_dir
        self._fps = fps
        self._max_width = max_width
        self._ffmpeg = _find_ffmpeg()
        self._process: subprocess.Popen | None = None
        self._current_path: str = ""
        self._paused = False
        self._rec_w: int = 0
        self._rec_h: int = 0
        # Sidecar state — reset per-lap in start_lap()
        self._sample_interval = max(1, int(round(fps / max(0.1, sidecar_sample_hz))))
        self._frame_index = 0  # number of frames written to ffmpeg so far
        self._sidecar_entries: list[dict] = []
        self._session_id = ""
        self._lap = 0
        self._start_wall_ms = 0
        self._last_meta: tuple[int, float | None] | None = None
        self._force_next_sample = False
        self._effective_fps: float | None = None  # set by finish_lap remux logic
        self._thumbs_block: dict | None = None     # set by finish_lap sprite step
        os.makedirs(output_dir, exist_ok=True)

    @property
    def is_recording(self) -> bool:
        return self._process is not None

    def update_session_id(self, new_session_id: str):
        """Update the session_id for the current recording.

        The MP4 file will be renamed after ffmpeg closes in finish_lap().
        The sidecar uses the updated id immediately."""
        if self._session_id == new_session_id:
            return
        old_id = self._session_id
        self._session_id = new_session_id
        logger.info("Recorder session_id updated: %s -> %s", old_id, new_session_id)

    def _calc_recording_size(self, src_w: int, src_h: int) -> tuple[int, int]:
        """Recording dimensions. By default (max_width<=0) inherits the
        captured screen size as-is. A positive max_width caps the width and
        scales height proportionally. Width and height are always rounded
        down to even numbers (libx264 requirement)."""
        if self._max_width <= 0 or src_w <= self._max_width:
            w, h = src_w, src_h
        else:
            scale = self._max_width / src_w
            w = self._max_width
            h = int(src_h * scale)
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
                    "-preset", "fast",
                    "-crf", "28",
                    "-g", str(self._fps),  # keyframe every 1s for fast seeking
                    "-pix_fmt", "yuv420p",
                    # moov atom at the front so HTTP Range-streaming clients
                    # (telemetry-comparison) can decode the first frame after
                    # fetching only the head of the file, not the whole MP4.
                    "-movflags", "+faststart",
                    filepath,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._current_path = filepath
            self._paused = False
            # Reset sidecar state for the new lap
            self._frame_index = 0
            self._sidecar_entries = []
            self._session_id = session_id
            self._lap = lap
            self._start_wall_ms = int(time.time() * 1000)
            self._last_meta = None
            self._force_next_sample = True  # always sample frame 0
            self._effective_fps = None      # will be set in finish_lap()
            self._thumbs_block = None       # will be set in finish_lap() sprite step
            logger.info(
                "Recording started: %s (%dx%d @ %dfps, sidecar every %d frames)",
                filename, self._rec_w, self._rec_h, self._fps, self._sample_interval,
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
        Automatically resizes to the recording resolution if needed.

        Pair with log_frame() to record sidecar metadata for this frame."""
        if self._process is None or self._paused:
            return
        h, w = frame.shape[:2]
        if w != self._rec_w or h != self._rec_h:
            frame = cv2.resize(frame, (self._rec_w, self._rec_h))
        try:
            self._process.stdin.write(frame.tobytes())
            self._frame_index += 1
        except (BrokenPipeError, OSError):
            logger.error("ffmpeg pipe broken, stopping recording")
            self._cleanup_process()

    def resize_to_recording(self, frame: np.ndarray) -> np.ndarray:
        """Resize a captured frame to the recording target size using the exact
        same computation write_frame() applies, so the two can't drift.

        Idempotent: a frame already at the target size is returned unchanged
        (write_frame likewise skips its own resize when the size already
        matches). The source uses this to pre-resize frames destined for its
        pre-roll buffer, keeping buffered frames small and letting write_frame
        pass them straight through on flush."""
        h, w = frame.shape[:2]
        tw, th = self._calc_recording_size(w, h)
        if (w, h) != (tw, th):
            frame = cv2.resize(frame, (tw, th))
        return frame

    def log_frame(self, wall_ms: int, norm_pos: float | None):
        """Record sidecar metadata for the most recently written frame.

        Must be called immediately after write_frame() so that frame index,
        wall_ms, and norm_pos line up. No-op if recording stopped/paused or
        no frame has been written yet."""
        if self._process is None or self._paused or self._frame_index == 0:
            return
        idx = self._frame_index - 1
        self._last_meta = (int(wall_ms), norm_pos)
        if (idx % self._sample_interval) == 0 or self._force_next_sample:
            self._record_sample(idx, wall_ms, norm_pos)
            self._force_next_sample = False

    def _record_sample(self, idx: int, wall_ms: int, norm_pos: float | None):
        if self._sidecar_entries and self._sidecar_entries[-1]["idx"] == idx:
            return  # already recorded this frame (e.g., pause boundary collision)
        self._sidecar_entries.append({
            "idx": idx,
            "t_ms": int(round(idx * 1000.0 / self._fps)),
            "wall_ms": int(wall_ms),
            "normPos": float(norm_pos) if norm_pos is not None else None,
        })

    def pause(self):
        """Pause recording — frames are skipped until resume().
        Forces a sample at the last LIVE frame so the wall-clock gap is bounded."""
        if not self._paused and self._last_meta is not None and self._frame_index > 0:
            wall_ms, norm_pos = self._last_meta
            self._record_sample(self._frame_index - 1, wall_ms, norm_pos)
        self._paused = True

    def resume(self):
        """Resume recording after a pause. The next logged frame is forced
        to be a sample so the post-pause wall_ms is anchored."""
        self._paused = False
        self._force_next_sample = True

    def finish_lap(self) -> str:
        """Finalize the current MP4, remux to the actual capture fps if it
        diverges from the declared rate, and write the sidecar JSON."""
        if self._process is None:
            return ""
        path = self._current_path
        # Anchor the very last frame in the sidecar so end-of-lap interpolation
        # is exact rather than capped at the prior sample.
        if self._last_meta is not None and self._frame_index > 0:
            wall_ms, norm_pos = self._last_meta
            self._record_sample(self._frame_index - 1, wall_ms, norm_pos)
        try:
            self._process.stdin.close()
            self._process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg did not finish in time, killing")
            self._process.kill()
        except Exception:
            logger.exception("Error finalizing recording")
            self._process.kill()

        # The capture loop targets self._fps but rarely sustains it on a
        # machine that's also running AC + ffmpeg. ffmpeg was told to mux
        # frames at self._fps so the MP4 ends up "compressed in time" by
        # whatever ratio the capture missed. Detect the actual rate from
        # wall-clock timing and rewrite the MP4 timebase + sidecar t_ms
        # accordingly so the browser plays at real-time speed.
        effective_fps = self._fps
        actual_fps = self._compute_actual_fps()
        if actual_fps is not None and abs(actual_fps - self._fps) > 0.5:
            if self._remux_with_fps(path, actual_fps):
                effective_fps = actual_fps
                # Sidecar t_ms must match the new playback timeline.
                for entry in self._sidecar_entries:
                    entry["t_ms"] = int(round(entry["idx"] * 1000.0 / actual_fps))
                logger.info(
                    "Remuxed %s: declared %d fps, actual %.2f fps",
                    os.path.basename(path), self._fps, actual_fps,
                )
        self._effective_fps = effective_fps

        # If session_id was updated after start_lap() (deferred adoption from
        # telemetry), rename the MP4 now that ffmpeg has released it.
        path = self._rename_to_session_id(path)

        # Sprite sheet for marker-drag frame preview (Telemetry Explorer).
        # Best-effort: failure logs a warning and the proxy lazy-fallback
        # generates the sprite on first request from the Explorer instead.
        if path and self._frame_index > 0:
            duration_ms = int(round(self._frame_index * 1000.0 / effective_fps))
            try:
                self._thumbs_block = self._generate_sprite(path, duration_ms)
            except Exception:
                logger.exception("Sprite generation raised; continuing without thumbs")
                self._thumbs_block = None

        self._write_sidecar(path)
        self._cleanup_process()
        if path:
            logger.info("Recording finalized: %s", path)
        return path

    def _compute_actual_fps(self) -> float | None:
        """Estimate the real capture rate from the recorded wall-clock window.
        Returns None if there isn't enough data to be confident."""
        if self._frame_index < 30 or self._last_meta is None:
            return None
        last_wall_ms, _ = self._last_meta
        wall_span_ms = last_wall_ms - self._start_wall_ms
        if wall_span_ms <= 1000:  # less than a second of capture, skip
            return None
        # frame_index counts frames 0..N-1; their wall span is start..last,
        # giving (frame_index - 1) intervals.
        intervals = max(1, self._frame_index - 1)
        return intervals * 1000.0 / wall_span_ms

    # -------- sprite generation (marker-drag frame preview) ----------------
    # 100-tile sprite sheet rendered from the finalized MP4. Built in
    # finish_lap() right after the remux step — the MP4 file already has its
    # final timebase, so the per-tile sample times line up exactly with the
    # sidecar's t_ms field. Failure here is non-fatal: we log and skip
    # injecting the `thumbs` sidecar block; the proxy will lazy-generate the
    # sprite on first request from Telemetry Explorer instead. Spec ref:
    # dev-planning/marker-drag-frame-preview/spec.md §5.1.
    SPRITE_TILES = 100
    SPRITE_COLS = 10
    SPRITE_ROWS = 10
    SPRITE_TILE_H = 90  # tile width is computed from actual MP4 aspect ratio

    @staticmethod
    def sprite_path_for(mp4_path: str) -> str:
        """Return the sprite JPEG path that pairs with an MP4 path."""
        if mp4_path.lower().endswith(".mp4"):
            return mp4_path[:-4] + ".thumbs.jpg"
        return mp4_path + ".thumbs.jpg"

    def _probe_mp4_dimensions(self, mp4_path: str) -> tuple[int, int] | None:
        """ffprobe the first video stream for width/height. Returns None on
        any failure — caller falls back to internal _rec_w/_rec_h."""
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            # static_ffmpeg.add_paths() (already invoked in _find_ffmpeg) puts
            # ffprobe alongside ffmpeg, so a second which() should find it.
            try:
                import static_ffmpeg
                static_ffmpeg.add_paths()
                ffprobe = shutil.which("ffprobe")
            except ImportError:
                pass
        if not ffprobe:
            return None
        try:
            proc = subprocess.run(
                [
                    ffprobe, "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-of", "csv=p=0:s=x",
                    mp4_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if proc.returncode != 0:
                return None
            out = proc.stdout.decode(errors="replace").strip()
            # Output format: "1920x1080"
            if "x" not in out:
                return None
            w_s, h_s = out.split("x", 1)
            return int(w_s), int(h_s)
        except Exception:
            return None

    def _generate_sprite(self, mp4_path: str, duration_ms: int) -> dict | None:
        """Run a single ffmpeg pass to produce a 10x10 JPEG sprite next to the
        MP4. Returns the `thumbs` sidecar block on success, None on failure."""
        if not mp4_path or not os.path.exists(mp4_path) or duration_ms <= 0:
            return None

        # Tile width — preserve actual MP4 aspect at height=90. Even-rounded
        # to keep ffmpeg's scale filter happy on hardware paths.
        dims = self._probe_mp4_dimensions(mp4_path)
        if dims is not None and dims[0] > 0 and dims[1] > 0:
            src_w, src_h = dims
        else:
            src_w, src_h = self._rec_w, self._rec_h
        if src_h <= 0 or src_w <= 0:
            return None
        tile_h = self.SPRITE_TILE_H
        tile_w = max(2, int(round(tile_h * src_w / src_h)))
        if tile_w % 2:
            tile_w += 1

        sprite_path = self.sprite_path_for(mp4_path)
        duration_s = duration_ms / 1000.0
        # fps=N/dur produces exactly N evenly spaced samples across the file.
        # scale+pad keeps aspect even if a future capture is non-16:9.
        vf = (
            f"fps={self.SPRITE_TILES}/{duration_s:.6f},"
            f"scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,"
            f"pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"tile={self.SPRITE_COLS}x{self.SPRITE_ROWS}"
        )
        try:
            proc = subprocess.run(
                [
                    self._ffmpeg, "-y",
                    "-i", mp4_path,
                    "-vf", vf,
                    "-frames:v", "1",
                    "-q:v", "5",
                    sprite_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Sprite generation failed (%d) for %s: %s",
                    proc.returncode,
                    os.path.basename(mp4_path),
                    proc.stderr.decode(errors="replace")[:300],
                )
                if os.path.exists(sprite_path):
                    try: os.remove(sprite_path)
                    except Exception: pass
                return None
        except Exception:
            logger.exception("Sprite generation crashed for %s", mp4_path)
            return None

        ms_per_tile = int(round(duration_ms / self.SPRITE_TILES))
        return {
            # `url` is the proxy route the frontend hits; the recorder doesn't
            # know the Explorer's session_id format, so we leave it as a
            # filename hint and let the proxy/frontend build the real URL.
            "url": os.path.basename(sprite_path),
            "tiles": self.SPRITE_TILES,
            "cols": self.SPRITE_COLS,
            "rows": self.SPRITE_ROWS,
            "tile_w": tile_w,
            "tile_h": tile_h,
            "ms_per_tile": ms_per_tile,
            "duration_ms": duration_ms,
        }

    def _remux_with_fps(self, mp4_path: str, fps: float) -> bool:
        """Rewrite the MP4 timebase to the actual fps using `-c copy` (no
        re-encode). Returns True on success."""
        if not mp4_path or not os.path.exists(mp4_path):
            return False
        tmp_path = mp4_path + ".tmp.mp4"
        try:
            proc = subprocess.run(
                [
                    self._ffmpeg, "-y",
                    "-i", mp4_path,
                    "-c", "copy",
                    # -r as output option sets the output timebase so the
                    # MP4 duration matches the actual capture wall-clock.
                    "-r", f"{fps:.4f}",
                    # Preserve fast-start: the input has the moov atom at the
                    # front from the initial encode, but `-c copy` without
                    # +faststart can re-emit it at the tail. Keep it up front
                    # so HTTP Range-streaming clients still get fast first paint.
                    "-movflags", "+faststart",
                    tmp_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Remux failed (%d): %s",
                    proc.returncode,
                    proc.stderr.decode(errors="replace")[:500],
                )
                if os.path.exists(tmp_path):
                    try: os.remove(tmp_path)
                    except Exception: pass
                return False
            os.replace(tmp_path, mp4_path)
            return True
        except Exception:
            logger.exception("Failed to remux MP4: %s", mp4_path)
            if os.path.exists(tmp_path):
                try: os.remove(tmp_path)
                except Exception: pass
            return False

    def _rename_to_session_id(self, path: str) -> str:
        """Rename the MP4 if session_id changed since start_lap().
        Called after ffmpeg has closed the file. Returns the (possibly new) path."""
        if not path or not os.path.exists(path):
            return path
        safe_id = self._session_id.replace(":", "-")
        expected_name = f"{safe_id}_lap{self._lap:03d}.mp4"
        current_name = os.path.basename(path)
        if current_name == expected_name:
            return path
        new_path = os.path.join(os.path.dirname(path), expected_name)
        try:
            os.rename(path, new_path)
            logger.info("Renamed recording: %s -> %s", current_name, expected_name)
            return new_path
        except OSError:
            logger.exception("Failed to rename %s -> %s", current_name, expected_name)
            return path

    def _write_sidecar(self, mp4_path: str) -> str:
        """Write <mp4>.sync.json next to the MP4. Returns the sidecar path
        or empty string on failure / nothing to write."""
        if not mp4_path or self._frame_index == 0:
            return ""
        sidecar_path = self.sidecar_path_for(mp4_path)
        # _effective_fps is set in finish_lap() — falls back to declared rate
        # if remux didn't run / wasn't needed.
        fps = getattr(self, "_effective_fps", self._fps) or self._fps
        duration_ms = int(round(self._frame_index * 1000.0 / fps))
        payload = {
            "session_id": self._session_id,
            "lap": self._lap,
            "start_wall_ms": self._start_wall_ms,
            "fps": fps,
            "duration_ms": duration_ms,
            "frame_count": self._frame_index,
            "frames": self._sidecar_entries,
        }
        # Sprite sheet metadata for the Telemetry Explorer marker-drag preview.
        # Absent when sprite generation failed — the proxy will fill it in on
        # first request.
        if self._thumbs_block is not None:
            payload["thumbs"] = self._thumbs_block
        try:
            with open(sidecar_path, "w") as f:
                json.dump(payload, f)
            logger.info(
                "Sidecar written: %s (%d samples, %d frames, %.1fs)",
                os.path.basename(sidecar_path),
                len(self._sidecar_entries),
                self._frame_index,
                duration_ms / 1000.0,
            )
            return sidecar_path
        except Exception:
            logger.exception("Failed to write sidecar JSON: %s", sidecar_path)
            return ""

    @staticmethod
    def sidecar_path_for(mp4_path: str) -> str:
        """Return the sidecar JSON path that pairs with an MP4 path."""
        if mp4_path.lower().endswith(".mp4"):
            return mp4_path[:-4] + ".sync.json"
        return mp4_path + ".sync.json"

    def _cleanup_process(self):
        self._process = None
        self._current_path = ""
        self._paused = False
        # Sidecar state will be reinitialized on the next start_lap()
