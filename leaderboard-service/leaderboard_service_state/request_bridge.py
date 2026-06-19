"""On-demand State read bridge: HTTP thread <-> stateful SDF, correlated by ``req_id``.

QuixStreams' native State (RocksDB) is reachable **only inside a stateful SDF
callback while processing a message for that key** — it cannot be queried from
the uvicorn worker thread, and (by design) **no leaderboard payload is kept in
RAM between requests**. To serve ``GET /live-positions`` the HTTP thread therefore
round-trips *through* the SDF:

1. The HTTP thread calls :meth:`PendingRequests.open` to register a unique
   ``req_id`` — a single-slot result holder + a :class:`threading.Event`.
2. It produces a synthetic ``{"type":"get_request","experiment":...,"req_id":...}``
   message to the events topic, keyed by ``experiment``.
3. The stateful SDF, processing that message **in-context** for the experiment
   key, does ``state.get(experiment)`` and calls :meth:`PendingRequests.deliver`
   with the ``req_id`` and the (transient) payload — setting the slot + the Event.
4. The HTTP thread :meth:`PendingRequests.wait`-s on the Event (bounded timeout),
   reads the payload, builds the response, then **discards** it.

The payload lives in RAM only for the duration of one in-flight request; the slot
is deleted as soon as the waiter consumes it (or times out). Nothing
leaderboard-shaped persists between requests. This is NOT a database and NOT a
cache — only a short-lived correlation table of in-flight requests.

Thread-safety: the SDF (consumer) thread writes via :meth:`deliver`; the uvicorn
thread reads/removes via :meth:`wait` / :meth:`close`. A single lock guards the
slot dict; the per-slot :class:`threading.Event` does the cross-thread wakeup.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Slot:
    """A single in-flight request's result holder."""

    event: threading.Event = field(default_factory=threading.Event)
    payload: dict[str, Any] | None = None
    delivered: bool = False


class PendingRequests:
    """Thread-safe ``req_id`` -> result-slot registry for in-flight State reads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[str, _Slot] = {}

    def open(self) -> str:
        """Register a new in-flight request; return its unique ``req_id``."""
        req_id = uuid.uuid4().hex
        with self._lock:
            self._slots[req_id] = _Slot()
        return req_id

    def deliver(self, req_id: str, payload: dict[str, Any] | None) -> bool:
        """Deliver *payload* to the slot for *req_id* and wake its waiter.

        Called from the SDF processing thread. Returns ``False`` (no-op) when the
        slot is unknown — e.g. the waiter already timed out and closed it.
        """
        with self._lock:
            slot = self._slots.get(req_id)
            if slot is None:
                return False
            slot.payload = payload
            slot.delivered = True
        # Set outside the lock; Event has its own internal lock.
        slot.event.set()
        return True

    def wait(self, req_id: str, timeout: float) -> tuple[bool, dict[str, Any] | None]:
        """Block until the slot is delivered or *timeout* (s) elapses.

        Returns ``(delivered, payload)``. Called from the HTTP thread. The slot is
        always removed before returning (success or timeout), so the payload var
        cannot outlive the request. An unknown ``req_id`` returns ``(False, None)``.
        """
        with self._lock:
            slot = self._slots.get(req_id)
        if slot is None:
            return False, None
        delivered = slot.event.wait(timeout)
        with self._lock:
            self._slots.pop(req_id, None)
        if not delivered:
            return False, None
        return slot.delivered, slot.payload

    def close(self, req_id: str) -> None:
        """Remove the slot for *req_id* (idempotent cleanup)."""
        with self._lock:
            self._slots.pop(req_id, None)

    def pending_count(self) -> int:
        """Number of currently in-flight requests (for ``/healthz``)."""
        with self._lock:
            return len(self._slots)
