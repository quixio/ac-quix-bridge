"""
Custom QuixStreams Source that reads Assetto Corsa telemetry from shared memory.

Produces two message types:
  - Physics + Graphics (high-frequency) → main output topic
  - Static session metadata → session topic (on session change only)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

from quixstreams.sources import Source

from ac_reader import ACReader

logger = logging.getLogger(__name__)


class AssettoCorsaSource(Source):
    """Reads AC telemetry and produces to Kafka topics."""

    def __init__(self, name: str, session_topic):
        super().__init__(name=name)
        self._sample_rate_hz = int(os.environ.get("SAMPLE_RATE_HZ", "50"))
        self._session_topic = session_topic
        self._session_id = None
        self._last_session_key = None

    def _new_session_id(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _check_session_change(self, reader: ACReader):
        """Detect session change and publish static data to the session topic."""
        current_key = reader.get_session_key()
        if not current_key or current_key == self._last_session_key:
            return

        self._last_session_key = current_key
        self._session_id = self._new_session_id()

        static_data = reader.read_static()
        static_data["session_id"] = self._session_id
        static_data["timestamp_ms"] = int(time.time() * 1000)

        # Use the session topic's serializer and produce via the low-level producer,
        # since Source.serialize()/produce() only support the main topic.
        msg = self._session_topic.serialize(
            key=self._session_id,
            value=static_data,
        )
        self.producer.produce(
            topic=self._session_topic.name,
            key=msg.key,
            value=msg.value,
            headers=msg.headers,
        )
        logger.info(
            "New session detected: %s | car=%s track=%s",
            self._session_id, static_data["carModel"], static_data["track"],
        )

    def run(self):
        reader = ACReader()
        interval = 1.0 / self._sample_rate_hz

        while self.running:
            if not reader.is_open:
                try:
                    reader.open()
                except FileNotFoundError:
                    logger.warning(
                        "AC shared memory not available — is Assetto Corsa running? "
                        "Retrying in 5 seconds..."
                    )
                    time.sleep(5)
                    continue

            try:
                # Check for session change and publish static data
                self._check_session_change(reader)

                # Read and publish physics + graphics
                data = reader.read_physics_and_graphics()
                data["session_id"] = self._session_id
                data["timestamp_ms"] = int(time.time() * 1000)

                msg = self.serialize(
                    key=self._session_id,
                    value=data,
                )
                self.produce(
                    key=msg.key,
                    value=msg.value,
                )
                logger.debug("Produced:\n%s", json.dumps(data, indent=2))
            except Exception:
                logger.exception("Error reading telemetry, reconnecting...")
                reader.close()
                self._last_session_key = None
                time.sleep(5)
                continue

            time.sleep(interval)

        reader.close()
