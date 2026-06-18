"""QuixStreams raw consumer (hot path) + in-memory best-laps index.

Subscribes to three topics on one ``Application``:

* ``ac-telemetry-raw`` — high-frequency ticks. Each tick is enriched
  (session + DCM caches), folded to the five-key group, and its ``iBestTime``
  written into the best-laps index as a monotonic per-group/driver minimum.
  Never touches the lake.
* ``ac-telemetry-session`` — per-session metadata; feeds the enrichment
  session cache and triggers a DCM refresh.
* ``ac-telemetry-config`` — DCM change events; refreshes the experiment
  cache between sessions.

Execution model: the consumer runs a **manual poll loop** over
``Application.get_consumer()`` on its own daemon thread; the FastAPI loop owns
the main thread. We deliberately do NOT call ``Application.run()``: that
helper installs ``SIGINT``/``SIGTERM`` handlers via ``signal.signal``, which
raises ``ValueError: signal only works in main thread of the main
interpreter`` when invoked from a worker thread (the main thread here is
uvicorn). ``get_consumer()`` returns a bare confluent consumer with no signal
setup, so the loop is safe off the main thread. This mirrors the proven
pattern in ``leaderboard-service/api/live_telemetry.py:_consumer_loop``.

State substrate: the queryable truth is the thread-safe
:class:`BestLapsStore` (``store.py``) that the HTTP API and the reconcile
worker read/write. :meth:`BestLapsStore.update_live` itself enforces the
monotonic per-group/driver minimum, so the index is the single authority for
"have we already seen a faster lap for this group?" — no separate durable
State store is consulted. (The previous SDF wrote a parallel RocksDB ``State``
store, but its warm-read-on-boot hook was never implemented, so it never fed
the API; dropping it alongside the SDF preserves observable behaviour.)
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .enrichment import Enrichment
from .settings import Settings
from .store import BEST_TIME_SENTINEL, BestLapsStore

logger = logging.getLogger(__name__)


class RawConsumer:
    """Owns the QuixStreams Application + the live best-laps updater thread."""

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
        # The poll loop checks ``self._stop`` every iteration (≤0.5 s poll
        # timeout), so setting the event is enough for a clean exit; the
        # ``with app.get_consumer()`` block closes the consumer on the way
        # out. No ``app.run()`` is in flight, so there is nothing to ``stop``.
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # -- raw tick handler --------------------------------------------------

    def _process_raw(self, value: dict[str, Any]) -> None:
        """Enrich one raw tick and fold its ``iBestTime`` into the best-laps
        index as a monotonic per-group/driver minimum.

        :meth:`BestLapsStore.update_live` is itself the min guard — it only
        accepts (and returns) a value when this lap is strictly faster than
        the stored best for the group, so no separate "stored best" lookup is
        needed. A non-positive ``iBestTime`` or a blank driver is a no-op.
        """
        try:
            i_best = int(value.get("iBestTime") or 0)
        except (TypeError, ValueError):
            return
        if i_best <= 0 or i_best >= BEST_TIME_SENTINEL:
            return
        fields = self._enrichment.enrich(value)
        driver = fields["driver"]
        if not driver:
            return
        self._store.update_live(
            fields["environment"],
            fields["experiment"],
            fields["track"],
            fields["carModel"],
            driver,
            i_best,
        )

    # -- thread body -------------------------------------------------------

    def _run(self) -> None:
        """Manual confluent-consumer poll loop on the worker thread.

        Uses ``Application.get_consumer()`` (a bare consumer, NO signal
        handlers) instead of ``Application.run()``: the latter calls
        ``signal.signal`` in ``_setup_signal_handlers``, which raises
        ``ValueError: signal only works in main thread`` off the main thread.
        Each message is deserialised with the matching ``app.topic`` and
        dispatched to the same handlers the old SDFs used. Offsets are
        auto-committed by the consumer (``auto_commit_enable=True`` default),
        matching the SDF's automatic-commit behaviour.
        """
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

        # Topic name → topic object, so the dispatcher picks the right
        # deserialiser without an isinstance check on the raw message.
        topics = {
            raw_topic.name: raw_topic,
            session_topic.name: session_topic,
            config_topic.name: config_topic,
        }

        logger.info(
            "best-laps consumer starting (topics=%s, %s, %s)",
            raw_topic.name,
            session_topic.name,
            config_topic.name,
        )
        try:
            with app.get_consumer() as consumer:
                consumer.subscribe(list(topics))
                while not self._stop.is_set():
                    msg = consumer.poll(timeout=0.5)
                    if msg is None:
                        continue
                    if msg.error():
                        logger.warning("kafka error: %s", msg.error())
                        continue
                    topic_obj = topics.get(msg.topic())
                    if topic_obj is None:
                        continue
                    try:
                        payload = topic_obj.deserialize(msg).value
                    except Exception:
                        logger.debug("deserialize failed; skip", exc_info=True)
                        continue
                    if not isinstance(payload, dict):
                        continue
                    try:
                        self._dispatch(msg.topic(), payload)
                    except Exception:
                        # A per-message handler error must not kill the loop.
                        logger.exception(
                            "handler error for topic=%s", msg.topic()
                        )
        except Exception:
            logger.exception("best-laps consumer crashed; exiting thread")
        finally:
            logger.info("best-laps consumer stopped")

    def _dispatch(self, topic_name: str, payload: dict[str, Any]) -> None:
        """Route one deserialised payload to the right handler by topic."""
        if topic_name == self._settings.raw_topic:
            self._process_raw(payload)
        elif topic_name == self._settings.session_topic:
            self._handle_session(payload)
        elif topic_name == self._settings.config_topic:
            self._enrichment.handle_config_event(payload)

    def _handle_session(self, value: dict[str, Any]) -> None:
        # The session topic's Kafka key is the hostname; we don't have the
        # Kafka key in this value-only path, so use the most-recent synthetic
        # hostname when the payload carries one, else a constant.
        hostname = str(value.get("hostname") or value.get("target_key") or "default")
        self._enrichment.handle_session_message(hostname, value)
