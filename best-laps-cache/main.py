"""Entry point: run the State-native SDF pipeline + serve the GET wrapper.

Durable store is QuixStreams' native State (RocksDB) on the ``state:`` volume —
no other database. The pipeline (``pipeline.py``) folds best laps into State; the
FastAPI app (``api.py``) serves ``GET /best-laps`` by round-tripping through the
SDF per request (produce a ``get_request`` event, read State in-context, deliver
the transient payload back via the ``PendingRequests`` bridge). No best-laps
payload persists in RAM between requests.

Threading model (resolves the signal-handler issue, spec §6.1/§8.2):

* ``Application.run()`` installs ``SIGINT``/``SIGTERM`` handlers via
  ``signal.signal`` — which raises off the main thread — so it runs on the
  **MAIN thread** (blocking).
* uvicorn runs on a **worker (daemon) thread**. ``uvicorn.Server.serve`` only
  installs signal handlers when on the main thread (``capture_signals`` is a
  no-op off-main-thread), so the HTTP server starts cleanly there.

On ``app.run()`` returning (SIGTERM), the process exits; the daemon HTTP thread
is torn down with it.
"""

from __future__ import annotations

import logging
import os
import sys
import threading

import uvicorn

from best_laps_cache.api import create_app
from best_laps_cache.enrichment import Enrichment
from best_laps_cache.pipeline import Pipeline
from best_laps_cache.request_bridge import PendingRequests
from best_laps_cache.settings import get_settings

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )


def _serve_http(pipeline: Pipeline, pending: PendingRequests, settings) -> None:
    """Run uvicorn on a worker thread (no signal handlers off-main-thread)."""
    app = create_app(pipeline, pending, settings)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.http_host,
            port=settings.http_port,
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
        )
    )
    server.run()


def main() -> int:
    _configure_logging()
    settings = get_settings()

    pending = PendingRequests()
    enrichment = Enrichment(settings)
    pipeline = Pipeline(settings, enrichment, pending)

    http_thread = threading.Thread(
        target=_serve_http,
        args=(pipeline, pending, settings),
        name="http-server",
        daemon=True,
    )
    http_thread.start()

    # Proactive cold-start lakehouse seed on a worker thread (never blocks the
    # main thread, which app.run() needs for its signal handlers). It queries the
    # lake ONCE (gated by the <state_dir>/.seeded marker) and produces one
    # per-experiment {"type":"seed"} message to best-laps-events; the SDF — once
    # running — folds each into State in-context. Producing onto the topic before
    # app.run() has fully started is safe: the messages persist until consumed.
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
