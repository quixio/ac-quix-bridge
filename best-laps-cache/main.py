"""Entry point: boot the three concurrent concerns and serve HTTP.

* Raw consumer (QuixStreams) — daemon thread, persistent State updater.
* Reconcile worker — daemon thread, serialized full-table scan.
* FastAPI / uvicorn — main thread, serves ``GET /best-laps``.

``uvicorn.run`` blocks the main thread; the two daemon threads run alongside
it. On shutdown (uvicorn returns) we signal both workers to stop and join.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from best_laps_cache.api import create_app
from best_laps_cache.consumer import RawConsumer
from best_laps_cache.enrichment import Enrichment
from best_laps_cache.reconcile import ReconcileWorker
from best_laps_cache.settings import get_settings
from best_laps_cache.store import BestLapsStore

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )


def main() -> int:
    _configure_logging()
    settings = get_settings()

    store = BestLapsStore()
    enrichment = Enrichment(settings)
    consumer = RawConsumer(settings, store, enrichment)
    reconcile = ReconcileWorker(settings, store)

    consumer.start()
    reconcile.start()

    app = create_app(store, settings)
    try:
        uvicorn.run(
            app,
            host=settings.http_host,
            port=settings.http_port,
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
        )
    finally:
        logger.info("shutting down workers")
        reconcile.stop()
        consumer.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
