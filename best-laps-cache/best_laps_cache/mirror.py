"""Thread-safe in-memory mirror of RocksDB state, keyed by experiment.

Written by the SDF thread on every successful fold (changed=True), read
directly by the HTTP thread on GET /best-laps. Eliminates the Kafka
round-trip that the old get_request/PendingRequests path required.
"""

from __future__ import annotations

import threading
from typing import Any


class BestLapsMirror:
    """Thread-safe in-memory mirror of RocksDB state, keyed by experiment."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[str, dict[str, Any]] = {}

    def update(self, experiment: str, payload: dict[str, Any]) -> None:
        """Called by SDF thread on every successful fold_lap (changed=True)."""
        with self._lock:
            self._data[experiment] = payload

    def get(self, experiment: str) -> dict[str, Any] | None:
        """Called by HTTP thread on GET /best-laps."""
        with self._lock:
            return self._data.get(experiment)

    def experiments(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())
