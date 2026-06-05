
"""
Assetto Corsa gameplay video capture (recording-only).

- Reads AC shared memory (graphics/static) for session and status detection
- Captures the game display via dxcam (DirectX Desktop Duplication)
- Records per-lap MP4 files locally via ffmpeg, then uploads to blob storage
- Consumes ac-telemetry-session to adopt the canonical session_id

State machine (mirrors ac-telemetry-source/ac_source.py):
  off/None → live:   new session → start recording
  pause → live:      resume (or new session if iCurrentTime dropped)
  live → pause:      pause recording
  live → off:        finalize MP4, upload to blob storage
  shm lost:          finalize MP4, upload to blob storage, reconnect
  lap change:        finalize current lap MP4, upload, start next lap
"""

import logging
import os
import threading
import time
from datetime import datetime, timezone

from session_tracker import SessionTracker
from video_recorder import VideoRecorder

logger = logging.getLogger(__name__)


def _get_blob_fs():
    """Build an s3fs filesystem for blob storage straight from
    Quix__BlobStorage__Connection__Json. We bypass quixportal.get_filesystem()
    because it doesn't expose an SSL-verify-disable knob, and the MinIO
    deployment uses a self-signed cert chain. Returns None on any failure
    (recording continues, MP4s stay local)."""
    import json
    raw = os.environ.get("Quix__BlobStorage__Connection__Json", "").strip()
    if not raw:
        logger.warning("Quix__BlobStorage__Connection__Json not set — MP4s stay local")
        return None
    try:
        cfg = json.loads(raw)
        s3 = cfg.get("S3Compatible") or cfg.get("s3_compatible") or {}
        bucket = s3.get("BucketName") or s3.get("bucket_name")
        endpoint = s3.get("ServiceUrl") or s3.get("service_url")
        access = s3.get("AccessKeyId") or s3.get("access_key_id")
        secret = s3.get("SecretAccessKey") or s3.get("secret_access_key")
        if not all([bucket, endpoint, access, secret]):
            raise ValueError("missing one of bucket/endpoint/access/secret")
        import fsspec
        fs = fsspec.filesystem(
            "s3",
            key=access,
            secret=secret,
            endpoint_url=endpoint,
            use_ssl=endpoint.startswith("https://"),
            # MinIO is fronted by a self-signed cert — skip verification.
            client_kwargs={"verify": False},
        )
        # Validate by listing the bucket root.
        fs.ls(f"{bucket}/", refresh=True)
        wrapped = fsspec.filesystem("dir", fs=fs, path=bucket)
        logger.info("Blob storage connected (s3://%s @ %s, SSL verify off)", bucket, endpoint)
        return wrapped
    except Exception as e:
        logger.warning("Blob storage not available, MP4s will remain local only: %s", e)
        return None


class ACVideoSource:
    """Captures AC gameplay and records per-lap MP4s."""

    def __init__(self, name: str):
        self.name = name
        # Lifecycle flag — formerly inherited from QuixStreams Source, now
        # owned outright so run() doesn't depend on the Application/Source
        # framework (which would otherwise force a Kafka output topic).
        self.running = True
        # Display selection: numeric index, "auto" (primary), or a resolution
        # like "3440x1440" that picks the matching output. Resolved in
        # _init_camera() after dxcam has enumerated outputs.
        self._display_selector = os.environ.get("VIDEO_DISPLAY_INDEX", "auto").strip()
        # Optional sub-rect crop within the chosen display, "left,top,right,bottom"
        # in display-local pixels. Useful on ultrawide screens to record only
        # the AC game viewport instead of the whole desktop.
        self._capture_region = self._parse_region(os.environ.get("VIDEO_CAPTURE_REGION", ""))
        self._fps = int(os.environ.get("VIDEO_FPS", "15"))
        self._output_dir = os.environ.get("VIDEO_OUTPUT_DIR", "./recordings")
        self._blob_prefix = os.environ.get("BLOB_VIDEO_PREFIX", "ac_video")
        self._recording_enabled = os.environ.get("VIDEO_RECORDING_ENABLED", "true").lower() == "true"
        self._recording_width = int(os.environ.get("RECORDING_WIDTH", "1920"))
        self._mock_mode = os.environ.get("AC_MOCK_MODE", "false").lower() == "true"
        self._blob_fs = _get_blob_fs() if self._recording_enabled else None
        self._session_tracker: SessionTracker | None = None

    def stop(self):
        self.running = False

    @staticmethod
    def _fallback_session_id() -> str:
        """Used only when the telemetry session topic is unreachable. Video
        recorded with this id won't be syncable in the Telemetry Explorer."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    @staticmethod
    def _parse_region(value: str) -> tuple[int, int, int, int] | None:
        """Parse "left,top,right,bottom" into a dxcam region tuple. Returns
        None when the env var is empty or malformed (caller falls back to
        full-display capture)."""
        if not value:
            return None
        try:
            parts = [int(p.strip()) for p in value.split(",")]
            if len(parts) != 4:
                raise ValueError("expected 4 comma-separated ints")
            left, top, right, bottom = parts
            if right <= left or bottom <= top:
                raise ValueError("right>left and bottom>top required")
            return left, top, right, bottom
        except Exception as e:
            logger.warning("Invalid VIDEO_CAPTURE_REGION %r (%s) — ignoring", value, e)
            return None

    def _enumerate_outputs(self, dxcam) -> list[dict]:
        """Return [{device, output, resolution, primary}, ...] across all GPU/output
        pairs. Parses dxcam.output_info() — a multi-line human-readable string
        whose format dxcam doesn't promise; we log it raw too so the user always
        has a fallback."""
        outputs: list[dict] = []
        try:
            raw = dxcam.output_info()
        except Exception:
            logger.exception("dxcam.output_info() failed; cannot enumerate displays")
            return outputs
        logger.info("dxcam.output_info():\n%s", raw)
        # Lines look like: "Device[0] Output[1]: Res:(3440, 1440) Rot:0 Primary:True"
        import re
        pattern = re.compile(
            r"Device\[(\d+)\]\s+Output\[(\d+)\].*?Res:\(\s*(\d+)\s*,\s*(\d+)\s*\).*?Primary:(\w+)",
            re.IGNORECASE,
        )
        for m in pattern.finditer(raw):
            outputs.append({
                "device": int(m.group(1)),
                "output": int(m.group(2)),
                "resolution": (int(m.group(3)), int(m.group(4))),
                "primary": m.group(5).strip().lower() == "true",
            })
        return outputs

    def _resolve_display(self, outputs: list[dict]) -> tuple[int | None, int | None]:
        """Map VIDEO_DISPLAY_INDEX to (device_idx, output_idx). Accepts:
          - "" or "auto"   → primary display
          - integer        → output_idx on device 0 (legacy behavior)
          - "WxH"          → first output whose resolution matches
        Returns (None, None) when no match."""
        sel = self._display_selector.lower()
        if not sel or sel == "auto":
            for o in outputs:
                if o["primary"]:
                    logger.info("Display selector 'auto' → primary %s", o)
                    return o["device"], o["output"]
            if outputs:
                logger.info("No primary flag found; using first output %s", outputs[0])
                return outputs[0]["device"], outputs[0]["output"]
            return None, None
        if "x" in sel and sel.replace("x", "").isdigit():
            try:
                w, h = (int(p) for p in sel.split("x", 1))
                for o in outputs:
                    if o["resolution"] == (w, h):
                        logger.info("Display selector '%s' matched %s", sel, o)
                        return o["device"], o["output"]
                logger.error("No display matches resolution %dx%d", w, h)
                return None, None
            except ValueError:
                pass
        try:
            idx = int(sel)
            logger.info("Display selector legacy index → device=0 output=%d", idx)
            return 0, idx
        except ValueError:
            logger.error("Unrecognized VIDEO_DISPLAY_INDEX value: %r", self._display_selector)
            return None, None

    def _init_camera(self):
        """Initialize dxcam screen capture. Returns (camera, (width, height)) or (None, None)."""
        try:
            import dxcam
        except ImportError:
            logger.error(
                "dxcam is not installed. Install it: pip install dxcam"
            )
            return None, None

        outputs = self._enumerate_outputs(dxcam)
        if outputs:
            logger.info(
                "Available displays:\n%s",
                "\n".join(
                    f"  device={o['device']} output={o['output']} "
                    f"{o['resolution'][0]}x{o['resolution'][1]} "
                    f"{'(primary)' if o['primary'] else ''}"
                    for o in outputs
                ),
            )
        device_idx, output_idx = self._resolve_display(outputs)
        if output_idx is None:
            return None, None

        try:
            create_kwargs = {"output_idx": output_idx}
            if device_idx is not None:
                create_kwargs["device_idx"] = device_idx
            if self._capture_region is not None:
                create_kwargs["region"] = self._capture_region
            camera = dxcam.create(**create_kwargs)
            frame = camera.grab()
            if frame is None:
                logger.error(
                    "Failed to grab initial frame from device=%s output=%d region=%s. "
                    "Is the display active and not in an RDP session?",
                    device_idx, output_idx, self._capture_region,
                )
                return None, None
            h, w = frame.shape[:2]
            logger.info(
                "Camera initialized: device=%s output=%d region=%s resolution %dx%d",
                device_idx, output_idx, self._capture_region, w, h,
            )
            return camera, (w, h)
        except Exception:
            logger.exception(
                "Failed to initialize dxcam (device=%s output=%s region=%s)",
                device_idx, output_idx, self._capture_region,
            )
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
        if not local_path:
            return
        if not self._blob_fs:
            # Log per-attempt so a one-time startup `Blob storage not
            # available` warning isn't the only trace — silent skips here
            # make it look like uploads are succeeding when they aren't.
            logger.warning(
                "blob_fs unavailable — skipping upload of %s (will stay local)",
                os.path.basename(local_path),
            )
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

        # Sprite sheet for marker-drag frame preview. Best-effort: missing
        # sprite is fine because the Telemetry Explorer proxy lazy-generates
        # it on first request when absent.
        sprite_path = VideoRecorder.sprite_path_for(local_path)
        if os.path.exists(sprite_path):
            self._upload_one(sprite_path, f"{folder}/{os.path.basename(sprite_path)}")

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
        # Single source of truth across producer (ac-telemetry-source) and
        # consumer (this recorder): both read `session_output` from the
        # shared root .env.
        session_topic_name = os.environ.get(
            "session_output",
            os.environ.get("session_input", "ac-telemetry-session"),
        )
        tracker = self._session_tracker

        def _run():
            consumer = None
            try:
                from quixstreams import Application as _App
                from quixstreams.kafka import ConnectionConfig as _CC
                conn = _CC(
                    bootstrap_servers=os.environ["Quix__Broker__Address"],
                    security_protocol="sasl_ssl",
                    sasl_mechanism="SCRAM-SHA-512",
                    sasl_username=os.environ["Quix__Broker__Username"],
                    sasl_password=os.environ["Quix__Broker__Password"],
                    enable_ssl_certificate_verification=False,
                    ssl_endpoint_identification_algorithm="none",
                )
                # Stable, workspace-prefixed consumer group name. Kafka ACLs
                # on this workspace authorize groups whose name starts with
                # the workspace id (same prefix QuixStreams auto-applies to
                # topics), so we mirror that pattern manually here — the
                # `Application(consumer_group=...)` kwarg does NOT auto-
                # prefix. Earlier code used a per-PID+timestamp suffix that
                # ACLs rejected with GROUP_AUTHORIZATION_FAILED, leaving
                # SessionTracker empty and the fallback session_id locked
                # in. With `auto_commit_enable=False` + `auto_offset_reset
                # ="earliest"`, every restart still reads from the topic
                # head since no offset is ever committed.
                # `Quix__Workspace__Id` is the canonical source; fall back to
                # `Quix__Broker__Username` which on this workspace happens to
                # equal the workspace id, so the ACL-compatible prefix works
                # without requiring the canonical var to be set.
                _ws = (
                    os.environ.get("Quix__Workspace__Id", "").strip()
                    or os.environ.get("Quix__Broker__Username", "").strip()
                )
                _group_suffix = "ac-video-streaming-session-tracker"
                _consumer_group = f"{_ws}-{_group_suffix}" if _ws else _group_suffix
                mini_app = _App(
                    consumer_group=_consumer_group,
                    auto_offset_reset="earliest",
                    auto_create_topics=False,
                    broker_address=conn,
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
        session_id_confirmed = True   # False while waiting for telemetry id
        session_detect_ms = 0         # wall-clock ms when new session was detected
        prev_norm_pos = None          # for start-line crossing detection
        waiting_for_start_line = False

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
                session_detect_ms = int(time.time() * 1000)
                # Non-blocking: use telemetry id if already available, else
                # start recording immediately with a temporary local id.
                resolved = (
                    self._session_tracker.try_get_fresh_session_id(session_detect_ms)
                    if self._session_tracker else None
                )
                if resolved:
                    session_id = resolved
                    session_id_confirmed = True
                else:
                    session_id = self._fallback_session_id()
                    session_id_confirmed = False
                    logger.info(
                        "Recording with temporary id %s — "
                        "waiting for telemetry session_id",
                        session_id,
                    )
                static_data = reader.read_static()
                logger.info(
                    "New session: %s (%s @ %s)%s",
                    session_id, static_data["carModel"], static_data["track"],
                    "" if session_id_confirmed else " [pending telemetry id]",
                )
                prev_completed_laps = completed_laps
                # Don't record yet — wait for the car to cross the
                # start/finish line so the MP4 has no pitstop footage.
                waiting_for_start_line = True
                prev_norm_pos = None

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
                    # Start new recording BEFORE uploading so the capture
                    # loop isn't blocked by the S3 transfer.
                    recorder.start_lap(session_id, completed_laps + 1, *display_size)
                    upload_sid = session_id
                    # Wrap so exceptions in the upload thread aren't
                    # silently swallowed (daemons don't surface their
                    # errors to the main loop).
                    def _bg_upload(p=path, sid=upload_sid):
                        try:
                            self._upload_to_blob(p, sid)
                        except Exception:  # noqa: BLE001
                            logger.exception("Background upload failed for %s", p)
                    threading.Thread(target=_bg_upload, daemon=True).start()
                prev_completed_laps = completed_laps

            # Detect start/finish line crossing to begin recording.
            # This skips the out-lap (pit → start line) so no pitstop
            # footage ends up in the MP4.
            if waiting_for_start_line and recorder and not recorder.is_recording:
                curr_norm = gfx.get("normalizedCarPosition")
                if curr_norm is not None and curr_norm < 0.05:
                    crossed = (prev_norm_pos is not None and prev_norm_pos > 0.9)
                    already_there = (prev_norm_pos is not None and prev_norm_pos < 0.1)
                    first_read = (prev_norm_pos is None)
                    if crossed or already_there or first_read:
                        # Gate recording on confirmed telemetry session_id.
                        # The outlap from pits to start-line is several
                        # seconds — the Kafka session message has had plenty
                        # of time to arrive. Refusing to start with a
                        # fallback id prevents the ms drift from being
                        # committed to blob.
                        if not session_id_confirmed and self._session_tracker is not None:
                            resolved = self._session_tracker.session_id_for_new_session(
                                session_detect_ms, timeout_s=5.0
                            )
                            if resolved and resolved != session_id:
                                logger.info(
                                    "Adopting telemetry session_id at start-line: "
                                    "%s (was temp %s)",
                                    resolved, session_id,
                                )
                                session_id = resolved
                                session_id_confirmed = True
                            elif not resolved:
                                logger.warning(
                                    "Start-line crossed but no telemetry session_id "
                                    "within 5s — recording with temp id %s; lap may "
                                    "not be syncable in Explorer",
                                    session_id,
                                )
                        recorder.start_lap(
                            session_id, completed_laps + 1, *display_size
                        )
                        waiting_for_start_line = False
                        logger.info(
                            "Start line crossed — recording begins (normPos %.3f)",
                            curr_norm,
                        )

            # Continuous reconciliation — every tick, check whether the
            # SessionTracker holds a session_id different from the one we're
            # currently recording under, and adopt it if so. This handles
            # both (a) the initial post-detect / pre-start-line gap where
            # Kafka hadn't delivered yet, AND (b) mid-session session_id
            # rotation (e.g. acc-telemetry-source detected an in-game
            # restart and published a fresh id while we were already
            # recording). Previously this was gated on
            # `session_id_confirmed` and stopped after first adoption.
            if self._session_tracker is not None:
                tracker_sid = self._session_tracker.current_session_id
                if tracker_sid and tracker_sid != session_id:
                    logger.info(
                        "Adopted telemetry session_id: %s (was %s)",
                        tracker_sid, session_id,
                    )
                    session_id = tracker_sid
                    session_id_confirmed = True
                    if recorder:
                        recorder.update_session_id(tracker_sid)
                elif (
                    not session_id_confirmed
                    and int(time.time() * 1000) - session_detect_ms > 15_000
                ):
                    # 15s timeout fallback — recording remains with the
                    # fallback id; the finish_lap rename + upload will use
                    # whatever id we have at that point.
                    logger.warning(
                        "No telemetry session_id received within 15s — "
                        "recording with temp id %s will not be syncable",
                        session_id,
                    )
                    session_id_confirmed = True  # stop warning

            # Capture frame
            frame = camera.grab()
            if frame is not None:
                timestamp_ms = int(time.time() * 1000)

                # Record to MP4 (fast — just writes raw bytes to ffmpeg pipe)
                if recorder and recorder.is_recording:
                    recorder.write_frame(frame)
                    # Re-read normPos now — the gfx from the top of the loop
                    # is stale (car moved between the read and frame capture).
                    old_norm = gfx.get("normalizedCarPosition")
                    try:
                        norm_pos = reader.read_graphics().get("normalizedCarPosition")
                        # Guard: if normPos wrapped backward (car crossed the
                        # finish line between reads), keep the old value so
                        # the crossing doesn't land in this lap's sidecar.
                        if old_norm is not None and old_norm > 0.8 and norm_pos is not None and norm_pos < 0.2:
                            norm_pos = old_norm
                    except Exception:
                        norm_pos = old_norm
                    recorder.log_frame(timestamp_ms, norm_pos)

            prev_status = status
            prev_current_time = current_time
            prev_norm_pos = gfx.get("normalizedCarPosition")

            # ---- Frame rate control ----
            now = time.perf_counter()
            if next_tick > now:
                time.sleep(next_tick - now)

        # ---- Cleanup on source shutdown ----
        self._finalize_recording(recorder, "source stopped", session_id or "")
        if camera is not None:
            del camera
        reader.close()
