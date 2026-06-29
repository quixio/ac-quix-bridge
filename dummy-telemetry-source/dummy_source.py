"""
Custom QuixStreams Source that replays a captured Assetto Corsa run.

Produces two message types, mirroring the live ``ac-telemetry-source``:
  - Raw telemetry (high-frequency) → main output topic, via ``self.produce``.
  - Static session metadata → session topic, via ``self.producer.produce``.

Unlike the live source this reads no shared memory: it loads a pre-captured,
already-validated corpus (``isValidLap=1`` on every record, ``iBestTime``
reconstructed as a running min of ``iLastTime``) and a session template, then
replays them in time order, paced to wall-clock divided by ``speedup``.

A fresh ``session_id`` is minted on every loop pass so each replay reads as a
new session downstream. Designed to generate VALID test data for the
best-laps cache / leaderboard.
"""

import gzip
import json
import logging
import time
from datetime import datetime, timezone

from quixstreams.sources import Source

logger = logging.getLogger(__name__)

# Maximum sleep between two consecutive records, in seconds. Guards against a
# pathological gap in the captured timestamps (e.g. a pause during capture)
# turning into a multi-minute stall on replay.
_MAX_SLEEP_S = 2.0


class DummyReplaySource(Source):
    """Replays a captured AC telemetry corpus to the raw and session topics."""

    def __init__(
        self,
        name: str,
        session_topic,
        corpus_path: str,
        session_template_path: str,
        speedup: float,
        hostname: str,
        loop: bool,
        max_messages: int,
    ):
        super().__init__(name=name)
        self._session_topic = session_topic
        self._corpus_path = corpus_path
        self._session_template_path = session_template_path
        self._speedup = speedup if speedup > 0 else 1.0
        self._hostname = hostname
        self._loop = loop
        self._max_messages = max_messages
        self._session_id = None

    def _new_session_id(self) -> str:
        # Mirrors ac_source._new_session_id: UTC ISO-8601 to ms + "Z".
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def _load_corpus(self) -> list[dict]:
        """Load the gzipped JSONL corpus once into a list of dicts."""
        records: list[dict] = []
        with gzip.open(self._corpus_path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                records.append(json.loads(line))
        return records

    def _load_session_template(self) -> dict:
        """Load the static session-metadata template once."""
        with open(self._session_template_path, encoding="utf-8") as fh:
            return json.load(fh)

    def _publish_session_metadata(self, template: dict) -> None:
        """Publish a fresh session payload to the session topic.

        Uses the secondary-topic produce idiom (``topic.serialize`` +
        ``self.producer.produce``) exactly as ac_source does, since a Source's
        own ``self.produce`` only targets the registered output topic.
        """
        payload = dict(template)
        payload["session_id"] = self._session_id
        payload["timestamp_ms"] = int(time.time() * 1000)

        msg = self._session_topic.serialize(key=self._hostname, value=payload)
        self.producer.produce(
            topic=self._session_topic.name,
            key=msg.key,
            value=msg.value,
            headers=msg.headers,
        )

    def run(self):
        records = self._load_corpus()
        template = self._load_session_template()
        logger.info(
            "Loaded corpus: %d records (%d fields), speedup=%.1fx, loop=%s, max_messages=%d",
            len(records),
            len(records[0]) if records else 0,
            self._speedup,
            self._loop,
            self._max_messages,
        )

        produced = 0
        loop_count = 0

        while self.running:
            loop_count += 1
            self._session_id = self._new_session_id()
            self._publish_session_metadata(template)
            logger.info("New session: %s (loop %d)", self._session_id, loop_count)

            prev_ts = None
            laps = 0
            for rec in records:
                if not self.running:
                    break

                # Pace to the original inter-record gap, scaled by speedup.
                ts = rec.get("timestamp_ms")
                if prev_ts is not None and ts is not None:
                    delay = (ts - prev_ts) / 1000.0 / self._speedup
                    delay = max(0.0, min(delay, _MAX_SLEEP_S))
                    if delay > 0:
                        time.sleep(delay)
                prev_ts = ts

                if not self.running:
                    break

                # Stamp with the current session + ingest wall-clock, mirroring
                # the live source (timestamp_ms is ingest time, not lap-relative).
                out = dict(rec)
                out["session_id"] = self._session_id
                out["timestamp_ms"] = int(time.time() * 1000)

                msg = self.serialize(key=self._hostname, value=out)
                self.produce(key=msg.key, value=msg.value)
                produced += 1

                completed = out.get("completedLaps")
                if isinstance(completed, int) and completed > laps:
                    laps = completed

                if self._max_messages and produced >= self._max_messages:
                    logger.info(
                        "Reached max_messages=%d; stopping after %d raw messages.",
                        self._max_messages,
                        produced,
                    )
                    return

            logger.info(
                "Completed replay loop %d: %d raw messages produced this pass, "
                "%d laps in corpus, %d total messages.",
                loop_count,
                len(records),
                laps,
                produced,
            )

            if not self._loop:
                logger.info("LOOP disabled; exiting after one pass.")
                break
