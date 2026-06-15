"""
Custom QuixStreams Source that reads Assetto Corsa Competizione telemetry from
shared memory.

Mirrors ac-telemetry-source/ac_source.py — see that file for design notes on
session detection. ACC uses the same ACC_STATUS enum values (0=off, 1=replay,
2=live, 3=pause) so the state machine is identical.

Session detection rules:
  - Rule A (entry/resume): prev_status != "live" AND current_status == "live".
    Fires a new session_id unless it's a plain `pause -> live` resume where
    iCurrentTime did NOT drop (= same lap continuing).
  - Rule B (in-game restart): prev_status == "live" AND current_status ==
    "live" AND iCurrentTime == 0 AND iLastTime == 2147483647 (ACC's INT32_MAX
    sentinel for "no prior lap") AND prev_iCurrentTime > 0. Catches the
    "Restart Session" button which resets lap counters without flipping
    status to pause/off — undetectable by Rule A alone.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

from quixstreams.sources import Source

from acc_reader import ACCReader


_SESSION_ID_FILE = Path(
    os.environ.get(
        "AC_SESSION_ID_FILE",
        Path(tempfile.gettempdir()) / "ac_quix_session_id.json",
    )
)

logger = logging.getLogger(__name__)

# ACC's "no prior lap" sentinel for iLastTime / iBestTime. AC uses 0 here;
# Kunos silently flipped the convention in ACC. Empirical, undocumented.
ACC_INT32_MAX_SENTINEL = 2147483647


class _UtcMsFormatter(logging.Formatter):
    """ISO-8601 UTC formatter with millisecond precision and trailing 'Z'."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z"


def configure_logging() -> None:
    """Install the UTC-ms formatter on the root handler, plus a rotating file log.

    Called from main.py after `logging.basicConfig` so the level is honoured but
    the format is upgraded. Also tees to a rotating file (default
    `logs/acc-source.log` next to this module, override with LOG_FILE) so session
    history survives the terminal closing on the sim PC.
    """
    fmt = _UtcMsFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    for h in root.handlers:
        h.setFormatter(fmt)

    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return  # idempotent: file handler already installed

    # File logging is best-effort — a read-only FS or bad LOG_FILE must not
    # crash the source (console logging is already up from basicConfig).
    log_file = Path(os.environ.get("LOG_FILE") or Path(__file__).resolve().parent / "logs" / "acc-source.log")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5)
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        logger.info("File logging enabled: %s (rotating 10MB x5)", log_file)
    except OSError as exc:
        logging.warning("File logging disabled: %s", exc)


class AssettoCorsaCompetizioneSource(Source):
    """Reads ACC telemetry and produces to Kafka topics."""

    def __init__(self, name: str, session_topic):
        super().__init__(name=name)
        self._sample_rate_hz = int(os.environ.get("SAMPLE_RATE_HZ", "50"))
        self._session_topic = session_topic
        self._session_id = None
        self._hostname = socket.gethostname()
        self._prev_status = None
        self._prev_current_time = None
        self._session_warmup_drop_ms = max(0, int(os.environ.get("SESSION_WARMUP_DROP_MS", "2000")))
        self._warmup_until = 0.0
        self._warmup_dropped = 0

    def _new_session_id(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _publish_session_metadata(self, reader: ACCReader):
        static_data = reader.read_static()
        static_data["session_id"] = self._session_id
        static_data["timestamp_ms"] = int(time.time() * 1000)

        msg = self._session_topic.serialize(
            key=self._hostname,
            value=static_data,
        )
        self.producer.produce(
            topic=self._session_topic.name,
            key=msg.key,
            value=msg.value,
            headers=msg.headers,
        )
        # Parallel disk handshake — write the same payload to a local file so
        # same-host consumers (notably `ac_video_streaming` on the AC bridge
        # PC) can pick up the canonical session_id without depending on Kafka
        # ACLs, consumer-group authorization, or subscribe-vs-publish race
        # conditions. Atomic write via tmp + os.replace. Best-effort: a
        # failure here does NOT block the Kafka publish.
        try:
            _SESSION_ID_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _SESSION_ID_FILE.with_suffix(_SESSION_ID_FILE.suffix + ".tmp")
            tmp.write_text(json.dumps(static_data))
            os.replace(tmp, _SESSION_ID_FILE)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to write session_id file at %s: %s", _SESSION_ID_FILE, e)

    def _check_session(
        self,
        status: str,
        current_time: int,
        last_time: int,
        speed_kmh: float,
        lap: int,
    ):
        """Decide whether the current tick begins a new session.

        Args:
            status: ACC status string ("off"/"replay"/"live"/"pause").
            current_time: iCurrentTime (current lap time in ms).
            last_time: iLastTime (last completed lap time; 2147483647 = no lap yet).
            speed_kmh: Current car speed; ACC restart teleports the car to
                pit/grid at speed=0, so we use this to distinguish a stable
                post-restart iCurrentTime=0 state from a transient lap-rollover
                iCurrentTime=0 at full speed.
            lap: completedLaps + 1 (for log context).

        Returns:
            True if a new session_id was assigned this tick.
        """
        new_session = False
        reason = ""

        # Rule A: prev != live AND current == live (entry / resume / restart-via-pause)
        if self._prev_status != "live" and status == "live":
            if self._prev_status is None or self._prev_status == "off":
                new_session = True
                reason = f"Rule A ({self._prev_status or 'init'} -> live)"
            elif self._prev_status == "pause":
                if self._prev_current_time is not None and current_time < self._prev_current_time:
                    new_session = True
                    reason = (
                        f"Rule A (pause -> live, iCT dropped "
                        f"{self._prev_current_time} -> {current_time})"
                    )
                else:
                    logger.info(
                        "status: pause -> live (iCT=%d, lap=%d) — resume same session",
                        current_time, lap,
                    )
            else:
                new_session = True
                reason = f"Rule A ({self._prev_status} -> live)"

        # Rule B: live -> live in-game restart (lap counters reset, status didn't flip).
        # The speed guard distinguishes a stable post-restart iCT=0 (car parked at
        # pit/grid) from a transient lap-rollover iCT=0 (car crossing start line at
        # full speed). At 50 Hz the lake-data evidence is that we never catch the
        # rollover transient, but the guard makes Rule B safe at higher poll rates.
        elif (
            self._prev_status == "live"
            and status == "live"
            and current_time == 0
            and last_time == ACC_INT32_MAX_SENTINEL
            and self._prev_current_time is not None
            and self._prev_current_time > 0
            and speed_kmh < 5.0
        ):
            new_session = True
            reason = (
                f"Rule B (in-game restart: iCT {self._prev_current_time} -> 0, "
                f"iLastTime sentinel, speed={speed_kmh:.1f} km/h)"
            )

        # Log every status change even when no new session fires
        if self._prev_status is not None and self._prev_status != status:
            logger.info(
                "status: %s -> %s (iCT=%d, lap=%d)",
                self._prev_status, status, current_time, lap,
            )

        if new_session:
            prev_id = self._session_id
            self._session_id = self._new_session_id()
            logger.info(
                "New session %s (prev=%s, %s)",
                self._session_id, prev_id, reason,
            )

        # Session-end cleanup of the disk-handshake file. Only delete on
        # TERMINAL transitions (live → off/replay), NOT on pause —
        # `pause → live` is documented above as a "resume same session"
        # path that does NOT regenerate the session_id or call
        # `_publish_session_metadata`. Deleting on pause would leave the
        # file missing for the entire resumed-driving period.
        if (
            self._prev_status == "live"
            and status in ("off", "replay")
            and _SESSION_ID_FILE.exists()
        ):
            try:
                _SESSION_ID_FILE.unlink()
                logger.info("Session ended (live → %s) — cleared handshake file %s",
                            status, _SESSION_ID_FILE)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to clear session_id file %s: %s", _SESSION_ID_FILE, e)

        self._prev_status = status
        self._prev_current_time = current_time
        return new_session

    def run(self):
        reader = ACCReader()
        interval = 1.0 / self._sample_rate_hz
        next_tick = None

        while self.running:
            if not reader.is_open:
                try:
                    reader.open()
                except FileNotFoundError:
                    logger.warning(
                        "ACC shared memory not available — is ACC running? Retrying in 5s..."
                    )
                    time.sleep(5)
                    next_tick = None
                    continue

            if next_tick is None:
                next_tick = time.perf_counter()

            next_tick += interval

            try:
                data = reader.read_physics_and_graphics()
                status = data["status"]
                current_time = data["iCurrentTime"]
                last_time = data["iLastTime"]
                speed_kmh = data["speedKmh"]
                lap = data["completedLaps"] + 1

                new_session = self._check_session(
                    status, current_time, last_time, speed_kmh, lap
                )
                if new_session:
                    self._publish_session_metadata(reader)
                    if self._session_warmup_drop_ms > 0:
                        self._warmup_until = time.perf_counter() + self._session_warmup_drop_ms / 1000
                        self._warmup_dropped = 0

                if status != "live" or self._session_id is None:
                    now = time.perf_counter()
                    if next_tick > now:
                        time.sleep(next_tick - now)
                    continue

                # Drop the first SESSION_WARMUP_DROP_MS of raw on a new session:
                # the DCM session config (carModel/track) lags the raw stream, so
                # these ticks would join the previous session's config (the sliver).
                if time.perf_counter() < self._warmup_until:
                    self._warmup_dropped += 1
                    now = time.perf_counter()
                    if next_tick > now:
                        time.sleep(next_tick - now)
                    continue

                if self._warmup_dropped:
                    logger.info(
                        "Raw resumed: dropped %d warmup ticks (~%dms) for session %s",
                        self._warmup_dropped, self._session_warmup_drop_ms, self._session_id,
                    )
                    self._warmup_dropped = 0

                data["session_id"] = self._session_id
                data["timestamp_ms"] = int(time.time() * 1000)

                msg = self.serialize(
                    key=self._hostname,
                    value=data,
                )
                self.produce(
                    key=msg.key,
                    value=msg.value,
                )
            except Exception:
                logger.exception("Error reading telemetry, reconnecting...")
                reader.close()
                self._prev_status = None
                self._prev_current_time = None
                next_tick = None
                time.sleep(5)
                continue

            now = time.perf_counter()
            if next_tick > now:
                time.sleep(next_tick - now)

        reader.close()
