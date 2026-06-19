"""Entry point: run the State-native SDF pipeline + serve the FastAPI app.

Durable store is QuixStreams' native State (RocksDB) on the ``state:`` volume — no
other database. The pipeline (``leaderboard_service_state/pipeline.py``)
reconstructs each new best lap's gate vector from ``ac-telemetry-raw`` and folds it
into State; the FastAPI app serves ``GET /api/v1/leaderboard/live-positions`` by
round-tripping through the SDF per request (produce a ``get_request`` event, read
State in-context, deliver the transient payload back via the ``PendingRequests``
bridge). No leaderboard payload persists in RAM between requests.

Threading model (three concerns, resolves the signal-handler issue):

* ``Application.run()`` (the SDF) installs ``SIGINT``/``SIGTERM`` handlers via
  ``signal.signal`` — which raises off the main thread — so it runs on the
  **MAIN thread** (blocking).
* uvicorn runs on a **worker (daemon) thread**. ``uvicorn.Server.serve`` only
  installs signal handlers on the main thread (``capture_signals`` is a no-op
  off-main-thread), so it starts cleanly there.
* the existing ``live_telemetry`` active-stream consumer keeps running on **its
  own worker thread**, started from the FastAPI lifespan (``api/app.py``). It uses
  a manual ``app.get_consumer()`` poll loop (NOT ``app.run()``), so it installs no
  signal handlers and coexists with the SDF on main.

The boot seed runs on a worker thread at startup (gated by ``<state_dir>/.seeded``).

``LOCAL_DEV_MODE=true`` keeps the legacy uvicorn-only behaviour (simulator, no SDF,
no broker) so local development without Kafka still works.
"""

from __future__ import annotations

import logging
import logging.config
import os
import sys
import threading
import time

import uvicorn
from asgi_correlation_id import CorrelationIdFilter

from api.app import create_app
from api.settings import get_settings
from leaderboard_service_state.enrichment import Enrichment
from leaderboard_service_state.pipeline import Pipeline
from leaderboard_service_state.request_bridge import PendingRequests
from leaderboard_service_state.runtime import Runtime, set_runtime
from leaderboard_service_state.settings import get_settings as get_state_settings

logger = logging.getLogger(__name__)

_LOG_FORMAT = (
    "%(asctime)s %(levelname)-7s %(name)s [req=%(correlation_id)s] %(message)s"
)


class _UtcIsoFormatter(logging.Formatter):
    """Render asctime as ISO 8601 UTC with millisecond precision and a Z suffix."""

    default_time_format = "%Y-%m-%dT%H:%M:%S"
    default_msec_format = "%s.%03dZ"

    @staticmethod
    def converter(timestamp: float | None) -> time.struct_time:
        return time.gmtime(timestamp)


def _build_log_config() -> dict:
    """Return a dictConfig that pipes every logger (incl. uvicorn) through one handler."""
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "correlation_id": {
                "()": CorrelationIdFilter,
                "default_value": "-",
            },
        },
        "formatters": {
            "default": {
                "()": _UtcIsoFormatter,
                "format": _LOG_FORMAT,
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "default",
                "filters": ["correlation_id"],
            },
        },
        "loggers": {
            "": {
                "level": os.getenv("LOG_LEVEL", "INFO").upper(),
                "handlers": ["default"],
            },
            "uvicorn": {"level": "INFO", "handlers": ["default"], "propagate": False},
            "uvicorn.access": {
                "level": "INFO",
                "handlers": ["default"],
                "propagate": False,
            },
            "uvicorn.error": {
                "level": "INFO",
                "handlers": ["default"],
                "propagate": False,
            },
        },
    }


def _serve_http(log_config: dict) -> None:
    """Run uvicorn on a worker thread (no signal handlers off-main-thread)."""
    settings = get_settings()
    server = uvicorn.Server(
        uvicorn.Config(
            app=create_app(),
            host=settings.api_host,
            port=settings.api_port,
            workers=settings.api_workers,
            log_config=log_config,
        )
    )
    server.run()


def main() -> int:
    log_config = _build_log_config()
    logging.config.dictConfig(log_config)

    is_local_dev = os.getenv("LOCAL_DEV_MODE", "false").lower() == "true"

    if is_local_dev:
        # Legacy uvicorn-only path: no SDF, no broker. The simulator + HTTP API
        # let the frontend render without Kafka / AC / a state volume.
        settings = get_settings()
        uvicorn.run(
            "api.app:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=True,
            reload_dirs=["/app/api"],
            reload_excludes=["*.pyc", "**/__pycache__/**"],
            log_config=log_config,
        )
        return 0

    state_settings = get_state_settings()
    pending = PendingRequests()
    enrichment = Enrichment(state_settings)
    pipeline = Pipeline(state_settings, enrichment, pending)
    set_runtime(Runtime(pipeline=pipeline, pending=pending, settings=state_settings))

    # uvicorn on a worker thread. Its FastAPI lifespan starts the live_telemetry
    # active-stream consumer on ITS own worker thread (concern 3).
    http_thread = threading.Thread(
        target=_serve_http,
        args=(log_config,),
        name="http-server",
        daemon=True,
    )
    http_thread.start()

    # Proactive cold-start lakehouse seed on a worker thread (gated by
    # <state_dir>/.seeded). Producing onto the events topic before app.run() has
    # fully started is safe: the messages persist until the SDF consumes them.
    boot_seed_thread = threading.Thread(
        target=pipeline.run_boot_seed,
        name="boot-seed",
        daemon=True,
    )
    boot_seed_thread.start()

    # Blocking; owns the main thread for the signal handlers app.run() installs.
    pipeline.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
