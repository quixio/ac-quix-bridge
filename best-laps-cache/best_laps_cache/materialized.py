"""Materialized current-view bridge: State (in-context only) → HTTP thread.

QuixStreams' native State (RocksDB) is reachable **only inside a stateful SDF
callback while processing a message for that key** — it cannot be queried from
the uvicorn worker thread. To still serve ``GET /best-laps`` over HTTP we keep a
tiny **materialized current view**: a per-experiment snapshot of the flattened
best-laps rows that the stateful read branch publishes whenever it processes a
session/config trigger or a new-best lap for that experiment.

This is NOT a second database and NOT the historical corpus — it is a small
in-process dict holding, per experiment key, the latest rows the SDF already
built via :func:`best_laps_cache.state_model.to_rows`. State (RocksDB) remains
the sole durable store; this view is a volatile read-through cache rebuilt from
State on every trigger and lost on restart (State recovers it).

Thread-safety: the SDF (processing) thread writes via :meth:`put`; the uvicorn
thread reads via :meth:`get_rows` / :meth:`active_experiment`. A single lock
guards the dict; rows are stored as already-built immutable lists.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class MaterializedView:
    """In-process per-experiment snapshot of built best-laps rows."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # experiment -> {"rows": [...], "environment": str, "as_of_epoch": float}
        self._by_experiment: dict[str, dict[str, Any]] = {}
        # The most-recently materialized experiment — the "active" one the GET
        # wrapper serves when no explicit experiment is requested.
        self._active_experiment: str | None = None

    def put(
        self,
        experiment: str,
        rows: list[dict[str, Any]],
        *,
        environment: str = "",
    ) -> None:
        """Replace the snapshot for *experiment* with freshly-built *rows*.

        Called from the stateful SDF read branch after it reads State and runs
        ``to_rows``. Marks *experiment* as the active one.
        """
        if not experiment:
            return
        snapshot = {
            "rows": list(rows),
            "environment": environment,
            "as_of_epoch": time.time(),
        }
        with self._lock:
            self._by_experiment[experiment] = snapshot
            self._active_experiment = experiment

    def active_experiment(self) -> str | None:
        with self._lock:
            return self._active_experiment

    def get_rows(self, experiment: str | None) -> tuple[list[dict[str, Any]], float | None]:
        """Return ``(rows, as_of_epoch)`` for *experiment*.

        When *experiment* is ``None`` the active (most-recently materialized)
        experiment is used. Unknown experiment → ``([], None)``.
        """
        with self._lock:
            key = experiment or self._active_experiment
            if not key:
                return [], None
            snapshot = self._by_experiment.get(key)
            if snapshot is None:
                return [], None
            return list(snapshot["rows"]), snapshot["as_of_epoch"]

    def experiments(self) -> list[str]:
        with self._lock:
            return list(self._by_experiment)
