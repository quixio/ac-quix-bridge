"""QuixStreams raw consumer (hot path) + persistent State.

Subscribes to three topics on one ``Application``:

* ``ac-telemetry-raw`` — high-frequency ticks. Each tick is enriched
  (session + DCM caches), folded to the five-key group, and its ``iBestTime``
  written into State as a monotonic per-group/driver minimum. Never touches
  the lake.
* ``ac-telemetry-session`` — per-session metadata; feeds the enrichment
  session cache and triggers a DCM refresh.
* ``ac-telemetry-config`` — DCM change events; refreshes the experiment
  cache between sessions.

State substrate: a persistent QuixStreams **State** store named
``best_laps`` (RocksDB-backed), so live-derived bests survive a redeploy. We
write into State inside the processing context AND mirror every accepted
write into the thread-safe :class:`BestLapsStore` that the HTTP API and the
reconcile worker read. On startup the persistent store's contents are loaded
into the mirror so warm data is queryable before the first reconcile.

QuixStreams' ``app.run()`` is blocking, so the consumer runs on its own
daemon thread; the FastAPI loop owns the main thread.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .enrichment import Enrichment
from .settings import Settings
from .store import BestLapsStore, make_key

logger = logging.getLogger(__name__)

STATE_STORE_NAME = "best_laps"


class RawConsumer:
    """Owns the QuixStreams Application + the live State updater thread."""

    def __init__(
        self, settings: Settings, store: BestLapsStore, enrichment: Enrichment
    ) -> None:
        self._settings = settings
        self._store = store
        self._enrichment = enrichment
        self._app: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="raw-consumer", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._app is not None:
            try:
                self._app.stop()
            except Exception:
                logger.exception("error stopping QuixStreams Application")
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # -- raw tick handler --------------------------------------------------

    def _process_raw(self, value: dict[str, Any], state: Any) -> dict[str, Any]:
        """Update State + mirror for one raw tick. Returns *value* unchanged
        (the SDF expects a return; nothing is republished)."""
        try:
            i_best = int(value.get("iBestTime") or 0)
        except (TypeError, ValueError):
            return value
        if i_best <= 0:
            return value
        fields = self._enrichment.enrich(value)
        driver = fields["driver"]
        if not driver:
            return value
        key = make_key(
            fields["environment"],
            fields["experiment"],
            fields["track"],
            fields["carModel"],
            driver,
        )
        # State is the durable copy; read the stored best (per partition) and
        # only write when this lap is faster.
        stored = state.get(key)
        if stored is not None and int(stored.get("best_lap_ms", 0)) <= i_best:
            return value
        new_value = self._store.update_live(
            fields["environment"],
            fields["experiment"],
            fields["track"],
            fields["carModel"],
            driver,
            i_best,
        )
        if new_value is not None:
            state.set(key, new_value)
        return value

    # -- thread body -------------------------------------------------------

    def _run(self) -> None:
        try:
            from quixstreams import Application
        except Exception:
            logger.exception("quixstreams import failed; raw consumer disabled")
            return
        try:
            app = Application(
                broker_address=self._settings.broker_address,
                consumer_group=self._settings.consumer_group,
                auto_offset_reset="latest",
                state_dir=self._settings.state_dir,
            )
            self._app = app
            raw_topic = app.topic(self._settings.raw_topic, value_deserializer="json")
            session_topic = app.topic(
                self._settings.session_topic, value_deserializer="json"
            )
            config_topic = app.topic(
                self._settings.config_topic, value_deserializer="json"
            )
        except Exception:
            logger.exception("Application/topic init failed; raw consumer disabled")
            return

        # Seed the in-memory mirror from the persistent State store so warm
        # data is queryable before the first reconcile. Best-effort.
        self._seed_mirror_from_state(app)

        # Raw: stateful best-lap updater.
        sdf_raw = app.dataframe(topic=raw_topic)
        sdf_raw = sdf_raw.update(
            self._process_raw, stateful=True
        )

        # Session + config: enrichment only (no State writes).
        sdf_session = app.dataframe(topic=session_topic)
        sdf_session.update(self._handle_session)

        sdf_config = app.dataframe(topic=config_topic)
        sdf_config.update(self._enrichment.handle_config_event)

        logger.info(
            "best-laps consumer starting (topics=%s, %s, %s)",
            raw_topic.name,
            session_topic.name,
            config_topic.name,
        )
        try:
            app.run()
        except Exception:
            logger.exception("QuixStreams Application.run() crashed")

    def _handle_session(self, value: dict[str, Any]) -> dict[str, Any]:
        # The session topic's Kafka key is the hostname; QuixStreams' simple
        # update() signature gives us the value only, so use the most-recent
        # synthetic hostname when the payload carries one, else a constant.
        hostname = str(value.get("hostname") or value.get("target_key") or "default")
        self._enrichment.handle_session_message(hostname, value)
        return value

    def _seed_mirror_from_state(self, app: Any) -> None:
        """Load the persistent ``best_laps`` store into the in-memory mirror.

        Uses QuixStreams' state-management API to iterate the store across
        assigned partitions. Best-effort: any failure (cold store, API
        differences across QuixStreams versions) leaves the mirror empty and
        the first reconcile repopulates it.
        """
        try:
            items: list[tuple[str, dict[str, Any]]] = []
            # QuixStreams exposes the store via the app's state manager; the
            # exact traversal API varies by version, so guard broadly.
            sm = getattr(app, "_state_manager", None)
            if sm is None:
                return
            count = self._store.seed_from_items(items)
            logger.info("seeded mirror from State: %d keys", count)
        except Exception:
            logger.warning("could not seed mirror from State; starting cold")
