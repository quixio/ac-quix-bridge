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
import math
import random
import time
from datetime import datetime, timezone

from quixstreams.sources import Source

logger = logging.getLogger(__name__)

# Maximum sleep between two consecutive records, in seconds. Guards against a
# pathological gap in the captured timestamps (e.g. a pause during capture)
# turning into a multi-minute stall on replay.
_MAX_SLEEP_S = 2.0

# Sentinel AC writes into iBestTime/iLastTime before the first valid lap closes.
# Records carrying this value (lap 1) are passed through untouched.
_INT_MAX = 2147483647


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
        base_best_ms: int,
        max_best_delta_ms: int,
        max_lap_offset_ms: int,
    ):
        super().__init__(name=name)
        self._session_topic = session_topic
        self._corpus_path = corpus_path
        self._session_template_path = session_template_path
        self._speedup = speedup if speedup > 0 else 1.0
        self._hostname = hostname
        self._loop = loop
        self._max_messages = max_messages
        self._base_best_ms = base_best_ms
        self._max_best_delta_ms = max_best_delta_ms
        self._max_lap_offset_ms = max_lap_offset_ms
        self._session_id = None
        # Per-lap best-time override state, reset at the start of every replay
        # loop so each loop re-randomizes (see _reset_lap_state / run).
        self._current_lap = None
        self._current_best = None
        # Per-lap live slow-down offset state (independent of the best-override
        # lap-state above so the two never interfere). _offset_amp is the
        # amplitude A drawn once per lap; _offset_lap is the completedLaps value
        # it was drawn for. Both reset per loop pass in _reset_lap_state.
        self._offset_lap = None
        self._offset_amp = None

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

    def _reset_lap_state(self) -> None:
        """Clear per-lap state so the next loop re-randomizes.

        Covers both the best-time override (``_current_lap``/``_current_best``)
        and the live slow-down offset (``_offset_lap``/``_offset_amp``).
        """
        self._current_lap = None
        self._current_best = None
        self._offset_lap = None
        self._offset_amp = None

    def _apply_best_override(self, out: dict) -> None:
        """Overwrite ``iBestTime``/``iLastTime`` with a per-lap random best.

        On the first valid-best tick of a lap (``completedLaps`` differs from
        the lap we last minted a best for) a fresh random best is drawn:
        ``base_best_ms - randint(0, max_best_delta_ms)``. That value is held
        CONSTANT for every subsequent valid tick of the same lap, so the
        downstream per-(track, car, driver) min-fold settles on exactly it
        rather than the per-tick minimum. INT_MAX ticks (lap 1, before any
        valid lap closes) are left untouched. Mutates ``out`` in place.
        """
        cl = out.get("completedLaps")
        ib = out.get("iBestTime")
        if not (isinstance(ib, int) and 0 < ib < _INT_MAX):
            return

        if cl != self._current_lap:
            self._current_lap = cl
            self._current_best = self._base_best_ms - random.randint(
                0, self._max_best_delta_ms
            )

        out["iBestTime"] = self._current_best
        out["iLastTime"] = self._current_best

    @staticmethod
    def _format_lap_time(ms: int) -> str:
        """Render a lap-relative duration in ms as the corpus ``currentTime`` format.

        The corpus uses ``"{m}:{s:02d}:{ms:03d}"`` (minutes : zero-padded
        seconds : zero-padded milliseconds), NOT the ``M:SS.mmm`` form the spec
        tentatively expected. Confirmed byte-for-byte against all 17272 corpus
        records (0 mismatches): e.g. 1005 -> "0:01:005", 83210 -> "1:23:210",
        176187 -> "2:56:187". Negative inputs are not produced for
        ``iCurrentTime`` (it is lap-relative and non-negative).
        """
        m = ms // 60000
        s = (ms // 1000) % 60
        millis = ms % 1000
        return f"{m}:{s:02d}:{millis:03d}"

    def _apply_lap_offset(self, out: dict) -> None:
        """Add a smooth per-lap slow-down offset to the LIVE lap-time fields.

        Owns EXACTLY these fields and no others:
          ``iCurrentTime``, ``currentTime``, ``iDeltaLapTime``,
          ``iEstimatedLapTime``, ``isDeltaPositive``.
        ``iBestTime``/``iLastTime`` remain owned solely by
        ``_apply_best_override`` and are never touched here.

        Amplitude ``A`` is drawn once per lap (mirroring ``_apply_best_override``):
        on the first tick whose ``completedLaps`` differs from the lap we last
        drew for, ``A = randint(0, max_lap_offset_ms)`` is sampled and held
        CONSTANT for the rest of that lap. The applied offset is shaped by lap
        progress::

            pos       = clamp(normalizedCarPosition, 0.0, 1.0)
            f(pos)    = sin(pi * pos)          # f(0)=f(1)=0, peak 1 @ pos=0.5
            offset_ms = round(A * f(pos))      # >= 0 (slow-down only)

        Because ``f`` is zero at both ends, the offset ramps in gradually and
        returns to zero at the start/finish line with NO discontinuity across
        the lap boundary (the acceptance criterion).

        Guards (pass the record through untouched, mirroring the
        ``_apply_best_override`` ``_INT_MAX`` guard):
          - feature disabled (``max_lap_offset_ms <= 0``);
          - ``normalizedCarPosition`` missing / non-numeric;
          - resulting amplitude is 0 (offset would be 0 anyway).

        String mirrors (OQ-1, confirmed from the corpus, §6.5/§7.4):
          - ``currentTime`` DOES track its int: re-derived via
            ``_format_lap_time`` from the mutated ``iCurrentTime``.
          - ``deltaLapTime`` is a FROZEN literal (``"-:--:---"`` in 100% of
            records, independent of ``iDeltaLapTime``) and
            ``estimatedLapTime`` is a FROZEN literal (``"35791:23:647"`` in
            100% of records, = INT_MAX rendered). The corpus never re-derives
            these from their ints, so we leave the strings as-is and mutate
            only the int fields — reproducing the corpus byte-for-byte.

        ``isDeltaPositive`` is stored as an int (0/1) in the corpus (never a
        Python bool), so it is recomputed as ``int(new_iDeltaLapTime >= 0)`` to
        preserve both the value coherence and the original type.
        """
        if self._max_lap_offset_ms <= 0:
            return

        pos = out.get("normalizedCarPosition")
        if not isinstance(pos, (int, float)):
            return

        cl = out.get("completedLaps")
        if cl != self._offset_lap:
            self._offset_lap = cl
            self._offset_amp = random.randint(0, self._max_lap_offset_ms)

        amp = self._offset_amp
        if not amp:
            return

        pos = min(1.0, max(0.0, float(pos)))
        offset_ms = round(amp * math.sin(math.pi * pos))
        if offset_ms <= 0:
            return

        ict = out.get("iCurrentTime")
        if isinstance(ict, int) and 0 <= ict < _INT_MAX:
            out["iCurrentTime"] = ict + offset_ms
            out["currentTime"] = self._format_lap_time(out["iCurrentTime"])

        idl = out.get("iDeltaLapTime")
        if isinstance(idl, int) and abs(idl) < _INT_MAX:
            new_idl = idl + offset_ms
            out["iDeltaLapTime"] = new_idl
            out["isDeltaPositive"] = int(new_idl >= 0)

        iel = out.get("iEstimatedLapTime")
        if isinstance(iel, int) and 0 <= iel < _INT_MAX:
            out["iEstimatedLapTime"] = iel + offset_ms

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

            # Fresh randoms per loop: otherwise loop N's completedLaps==1 would
            # reuse loop N-1's best (state carries the same lap numbers).
            self._reset_lap_state()
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
                self._apply_best_override(out)
                self._apply_lap_offset(out)
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
