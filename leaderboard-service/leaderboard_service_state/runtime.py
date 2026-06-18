"""Process-wide handles to the State pipeline + read bridge.

``main.py`` constructs the :class:`~leaderboard_service_state.pipeline.Pipeline`
and :class:`~leaderboard_service_state.request_bridge.PendingRequests` once and
registers them here. The FastAPI layer (running on the uvicorn worker thread)
reads them via :func:`get_runtime` to round-trip a per-request in-context State
read — there is no other shared state between the SDF thread and the HTTP thread
besides this bridge.

A module-level singleton (guarded by a lock) is the simplest cross-thread handoff:
both threads live in one process, and the pipeline is created before uvicorn
starts serving.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .pipeline import Pipeline
from .request_bridge import PendingRequests
from .settings import Settings


@dataclass(frozen=True)
class Runtime:
    pipeline: Pipeline
    pending: PendingRequests
    settings: Settings


_lock = threading.Lock()
_runtime: Runtime | None = None


def set_runtime(runtime: Runtime) -> None:
    global _runtime
    with _lock:
        _runtime = runtime


def get_runtime() -> Runtime | None:
    with _lock:
        return _runtime
