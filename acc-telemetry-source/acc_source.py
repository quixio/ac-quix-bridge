"""
Custom QuixStreams Source that reads Assetto Corsa Competizione telemetry from
shared memory.

Mirrors ac-telemetry-source/ac_source.py — see that file for design notes on
session detection. ACC uses the same ACC_STATUS enum values (0=off, 1=replay,
2=live, 3=pause) so the state machine is identical.
"""

import json
import logging
import os
import socket
import time
from datetime import datetime, timezone

from quixstreams.sources import Source

from acc_reader import ACCReader

logger = logging.getLogger(__name__)


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

    def _check_session(self, status: str, current_time: int):
        new_session = False

        if self._prev_status != "live" and status == "live":
            if self._prev_status is None or self._prev_status == "off":
                new_session = True
                logger.info("Session start detected (%s -> live)", self._prev_status or "init")
            elif self._prev_status == "pause":
                if self._prev_current_time is not None and current_time < self._prev_current_time:
                    new_session = True
                    logger.info(
                        "Session restart detected (pause -> live, iCurrentTime %d -> %d)",
                        self._prev_current_time, current_time,
                    )
                else:
                    logger.info("Session resumed (pause -> live)")
            else:
                new_session = True
                logger.info("Session start detected (%s -> live)", self._prev_status)

        if new_session:
            self._session_id = self._new_session_id()

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

                new_session = self._check_session(status, current_time)
                if new_session:
                    self._publish_session_metadata(reader)
                    logger.info("New session: %s", self._session_id)

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
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("Produced:\n%s", json.dumps(data, indent=2))
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
