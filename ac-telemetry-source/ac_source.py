"""
Custom QuixStreams Source that reads Assetto Corsa telemetry from shared memory.
"""

import logging
import os
import time
from datetime import datetime, timezone

from quixstreams.sources import Source

from ac_reader import ACReader

logger = logging.getLogger(__name__)


class AssettoCorsaSource(Source):
    """Reads AC physics telemetry and produces messages to a Kafka topic."""

    def __init__(self, name: str):
        super().__init__(name=name)
        self._sample_rate_hz = int(os.environ.get("SAMPLE_RATE_HZ", "50"))
        self._session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def run(self):
        reader = ACReader()
        interval = 1.0 / self._sample_rate_hz

        while self.running:
            # Connect / reconnect to shared memory
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
                data = reader.read()
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
                logger.debug("Produced: %s", data)
            except Exception:
                logger.exception("Error reading telemetry, reconnecting...")
                reader.close()
                time.sleep(5)
                continue

            time.sleep(interval)

        reader.close()
