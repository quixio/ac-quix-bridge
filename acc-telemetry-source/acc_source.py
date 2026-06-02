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

import logging
import os
import socket
import time
from datetime import datetime, timezone

from quixstreams.sources import Source

from acc_reader import ACCReader

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
    """Install the UTC-ms formatter on the root handler.

    Called from main.py after `logging.basicConfig` so the level is honoured
    but the format is upgraded.
    """
    root = logging.getLogger()
    for h in root.handlers:
        h.setFormatter(_UtcMsFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))


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

    def _check_session(self, status: str, current_time: int, last_time: int, lap: int):
        """Decide whether the current tick begins a new session.

        Args:
            status: ACC status string ("off"/"replay"/"live"/"pause").
            current_time: iCurrentTime (current lap time in ms).
            last_time: iLastTime (last completed lap time; 2147483647 = no lap yet).
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

        # Rule B: live -> live in-game restart (lap counters reset, status didn't flip)
        elif (
            self._prev_status == "live"
            and status == "live"
            and current_time == 0
            and last_time == ACC_INT32_MAX_SENTINEL
            and self._prev_current_time is not None
            and self._prev_current_time > 0
        ):
            new_session = True
            reason = (
                f"Rule B (in-game restart: iCT {self._prev_current_time} -> 0, "
                f"iLastTime sentinel)"
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
                lap = data["completedLaps"] + 1

                new_session = self._check_session(status, current_time, last_time, lap)
                if new_session:
                    self._publish_session_metadata(reader)

                if status != "live" or self._session_id is None:
                    now = time.perf_counter()
                    if next_tick > now:
                        time.sleep(next_tick - now)
                    continue

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
