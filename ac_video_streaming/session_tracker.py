"""
SessionTracker — adopts session_id from ac-telemetry-source.

A QuixStreams DataFrame callback feeds session messages from the
`ac-telemetry-session` topic into this thread-safe holder. The video source
queries it on off->live detection so the MP4 / sidecar / S3 path use the
SAME session_id as the telemetry pipeline (required for the Telemetry
Explorer to find the matching video).
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)


class SessionTracker:
    """Thread-safe holder for the latest telemetry session_id.

    Updated from a QuixStreams `sdf.update()` callback bound to
    `ac-telemetry-session`. Read by the video source on each new-session
    detection.
    """

    # When the video source detects off->live, telemetry may not yet have
    # published the new session message (or may have published slightly
    # earlier). Accept any session message whose timestamp is within
    # this many ms of the video source's detection time.
    FRESH_TOLERANCE_MS = 2000

    def __init__(self):
        self._lock = threading.Lock()
        self._session_id: str | None = None
        self._timestamp_ms: int = 0
        self._track: str = ""
        self._car_model: str = ""

    def update_from_message(self, value):
        """SDF callback — invoked for every message on the session topic."""
        if not isinstance(value, dict):
            logger.warning("Session topic message is not a dict: %r", value)
            return value
        sid = value.get("session_id")
        if not sid:
            return value
        ts = int(value.get("timestamp_ms", 0))
        with self._lock:
            prev = self._session_id
            self._session_id = sid
            self._timestamp_ms = ts
            self._track = value.get("track", "") or ""
            self._car_model = value.get("carModel", "") or ""
        if sid != prev:
            logger.info(
                "Adopted telemetry session_id=%s (track=%s, car=%s)",
                sid, self._track, self._car_model,
            )
        return value

    @property
    def current_session_id(self) -> str | None:
        with self._lock:
            return self._session_id

    @property
    def latest(self) -> dict:
        with self._lock:
            return {
                "session_id": self._session_id,
                "timestamp_ms": self._timestamp_ms,
                "track": self._track,
                "carModel": self._car_model,
            }

    def session_id_for_new_session(
        self, our_detect_ms: int, timeout_s: float = 2.0
    ) -> str | None:
        """Return the telemetry session_id appropriate for a session detected
        by the video source at `our_detect_ms` (wall-clock).

        Behavior:
        - If the tracker holds a session message with timestamp within
          ~2s of our_detect_ms (before or after), return that id immediately.
        - Otherwise wait up to timeout_s for telemetry to publish a fresh one.
        - On timeout, return whatever id we have (possibly stale — e.g. cold
          start where telemetry has been running and we just connected).
        - Return None only if no session message has ever been received.

        The caller falls back to a locally-generated id only on None.
        """
        deadline = time.time() + timeout_s
        while True:
            with self._lock:
                sid = self._session_id
                ts = self._timestamp_ms
            if sid is not None and ts >= our_detect_ms - self.FRESH_TOLERANCE_MS:
                return sid
            if time.time() >= deadline:
                return sid
            time.sleep(0.05)
