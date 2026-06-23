"""QuixStreams ``Application`` + ``StreamingDataFrame`` pipeline (State-native).

Durable store: QuixStreams' **native State (RocksDB)** on the ``state:`` volume —
no other database anywhere. State is keyed by ``experiment``; the value is the
nested payload ``{_env, track: {carModel: {driver: best_lap_ms}}}`` (see
:mod:`best_laps_cache.state_model`).

Topology (three SDF roots under one ``app.run()``):

* **Write branch** — ``ac-telemetry-raw`` → :meth:`Enrichment.enrich` → filter
  valid/non-stub ``iBestTime`` + driver + experiment → **pre-group_by de-dupe**
  (drop ticks whose ``iBestTime`` did not change for the native key; collapses
  the ~50 Hz stream to ~one message per new best) → re-key to ``experiment`` and
  produce a ``{"type":"lap", ...}`` event to the internal **events topic**.
* **Read-trigger branch** — ``ac-telemetry-session`` + ``ac-telemetry-config`` →
  feed the enrichment caches → resolve the active experiment → produce a
  ``{"type":"read", "experiment": ...}`` event to the **same events topic**,
  keyed by experiment. This is now **write-only**: it exists only to drive the
  lazy in-context seed of a genuinely empty experiment; it materialises nothing.
* **Stateful branch** — consumes the **events topic** (one source → one
  ``stream_id`` → one State store). ``type="lap"``/``type="seed"`` fold into
  ``state[experiment]`` (write-only); ``type="read"`` reads ``state[experiment]``
  and lazily seeds it from the lakehouse when empty (also write-only — it builds
  no served snapshot); ``type="get_request"`` reads ``state[experiment]`` and
  delivers the **transient** payload back to the waiting HTTP handler via the
  :class:`~best_laps_cache.request_bridge.PendingRequests` bridge, correlated by
  ``req_id``. No best-laps payload persists in RAM between requests — the
  former persistent materialized view is gone.

Why the events topic (not two ``group_by("experiment")`` branches sharing a
store): in QuixStreams 3.x a state store is scoped by ``stream_id``, and
``group_by`` derives a new ``stream_id`` per *source* (``"<src>--groupby--<op>"``).
Two branches from different source topics therefore land in **different** stores
even when grouping by the same column — QS's own docstring: *"StreamingDataFrames
with different stream_id cannot access the same state stores."* Funnelling both
producers into one experiment-keyed events topic gives literally one stateful
SDF, one ``stream_id``, one RocksDB store — the spec §5.1 path, still State-only.
"""

from __future__ import annotations

import logging
from typing import Any

from quixstreams import Application
from quixstreams.state import State

from .boot_seed import _GATE_FLAG, run_boot_seed
from .enrichment import Enrichment
from .request_bridge import PendingRequests
from .seed import seed_experiment_payload
from .settings import Settings
from .state_model import INT_MAX, fold_lap

logger = logging.getLogger(__name__)

# Internal topic that both producers (raw-write, session/config-trigger) feed and
# the single stateful SDF consumes. Experiment-keyed so all events for one
# experiment co-partition onto the same RocksDB instance.
EVENTS_TOPIC = "best-laps-events"

# Pre-group_by de-dupe store: last-seen iBestTime per native raw key.
_LAST_BEST_KEY = "_last_ibest"


def build_application(settings: Settings) -> Application:
    return Application(
        broker_address=settings.broker_address,
        consumer_group=settings.consumer_group,
        auto_offset_reset="latest",
        state_dir=settings.state_dir,
    )


class Pipeline:
    """Builds and owns the three-root SDF topology over one Application."""

    def __init__(
        self,
        settings: Settings,
        enrichment: Enrichment,
        pending: PendingRequests,
    ) -> None:
        self._settings = settings
        self._enrichment = enrichment
        self._pending = pending
        self._app = build_application(settings)
        # Set in _build(); the boot seeder serialises against this exact Topic
        # so its messages ride the same JSON contract the SDF consumes.
        self._events_topic: Any = None
        self._build()

    @property
    def app(self) -> Application:
        return self._app

    # -- topology ----------------------------------------------------------

    def _build(self) -> None:
        app = self._app
        s = self._settings

        raw_topic = app.topic(s.raw_topic, value_deserializer="json")
        session_topic = app.topic(s.session_topic, value_deserializer="json", key_deserializer="str")
        config_topic = app.topic(s.config_topic, value_deserializer="json")
        events_topic = app.topic(
            EVENTS_TOPIC,
            value_deserializer="json",
            value_serializer="json",
            key_deserializer="str",
            key_serializer="str",
        )
        self._events_topic = events_topic

        # -- write branch: raw -> events("lap") ---------------------------
        sdf_raw = app.dataframe(raw_topic)
        sdf_raw = sdf_raw.apply(self._enrich_raw)
        sdf_raw = sdf_raw.filter(
            lambda v: (
                bool(v)
                and 0 < v["best_ms"] < INT_MAX
                and bool(v["driver"])
                and bool(v["experiment"])
                and bool(v["track"])
                and bool(v["carModel"])
            )
        )
        # Pre-group_by de-dupe: only let through ticks whose iBestTime changed
        # for this native key, collapsing the ~50 Hz raw stream to ~one message
        # per new best before it ever reaches the events topic (repartition).
        sdf_raw = sdf_raw.filter(self._is_new_best, stateful=True)
        sdf_raw.to_topic(events_topic, key=lambda v: v["experiment"])

        # -- read-trigger branch: session + config -> events("read") ------
        sdf_session = app.dataframe(session_topic)
        sdf_session = sdf_session.apply(self._resolve_session, metadata=True)
        sdf_session = sdf_session.filter(lambda v: bool(v) and bool(v["experiment"]))
        sdf_session.to_topic(events_topic, key=lambda v: v["experiment"])

        sdf_config = app.dataframe(config_topic)
        sdf_config = sdf_config.apply(self._resolve_config)
        sdf_config = sdf_config.filter(lambda v: bool(v) and bool(v["experiment"]))
        sdf_config.to_topic(events_topic, key=lambda v: v["experiment"])

        # -- stateful branch: events -> State (write) / get_request (read) -
        sdf_events = app.dataframe(events_topic)
        sdf_events.update(self._handle_event, stateful=True)

    # -- write-branch callbacks -------------------------------------------

    def _enrich_raw(self, value: dict[str, Any]) -> dict[str, Any]:
        """Enrich one raw tick to the five-key group + best_ms.

        Reuses :meth:`Enrichment.enrich` verbatim. Returns a normalized dict;
        invalid ticks return a sentinel the downstream ``filter`` drops.

        When ``settings.valid_laps_only`` is ``True`` (default), reads
        ``iBestTime`` (only AC-valid laps carry a positive value here). When
        ``False``, reads ``iLastTime`` (the most-recently-completed lap time,
        valid or not); the existing ``0 < best_ms < INT_MAX`` filter downstream
        still rejects sentinels.
        """
        try:
            if self._settings.valid_laps_only:
                best_ms = int(value.get("iBestTime") or 0)
            else:
                best_ms = int(value.get("iLastTime") or 0)
        except (TypeError, ValueError):
            return {}
        fields = self._enrichment.enrich(value)
        return {
            "type": "lap",
            "experiment": fields["experiment"],
            "environment": fields["environment"],
            "track": fields["track"],
            "carModel": fields["carModel"],
            "driver": fields["driver"],
            "best_ms": best_ms,
        }

    def _is_new_best(self, value: dict[str, Any], state: State) -> bool:
        """Drop ticks whose ``best_ms`` did not improve for this native key.

        Keyed by the raw message's native Kafka key (this op runs BEFORE the
        re-key to experiment), so it dedupes the high-frequency per-stream
        ticks. Only a strictly-faster (or first-seen) ``best_ms`` proceeds to
        the events topic, slashing repartition traffic (spec §8.3).
        """
        best_ms = int(value["best_ms"])
        last = state.get(_LAST_BEST_KEY)
        if last is not None and best_ms >= int(last):
            return False
        state.set(_LAST_BEST_KEY, best_ms)
        return True

    # -- read-trigger callbacks -------------------------------------------

    def _resolve_session(
        self, value: dict[str, Any], key: Any, timestamp: int, headers: Any
    ) -> dict[str, Any]:
        """Feed the session cache + DCM refresh, then resolve the active
        experiment. Returns ``{"type":"read","experiment":...}`` (dropped
        downstream if the experiment is unresolved).

        ``key`` is the Kafka message key (the stream/hostname identifier, e.g.
        ``"QUIX-GAMING"``). It is decoded from bytes when necessary. The
        fallback chain is: Kafka key → payload ``"hostname"`` → payload
        ``"target_key"`` → ``"default"``.
        """
        if isinstance(key, bytes):
            key = key.decode("utf-8", errors="replace")
        hostname = str(key or value.get("hostname") or value.get("target_key") or "default")
        try:
            self._enrichment.handle_session_message(hostname, value)
        except Exception:  # noqa: BLE001 — a bad session msg must not stall
            logger.exception("session enrichment failed")
        return self._active_read_event()

    def _resolve_config(self, value: dict[str, Any]) -> dict[str, Any]:
        try:
            self._enrichment.handle_config_event(value)
        except Exception:  # noqa: BLE001
            logger.exception("config enrichment failed")
        return self._active_read_event()

    def _active_read_event(self) -> dict[str, Any]:
        fields = self._enrichment.enrich({})
        experiment = fields.get("experiment") or ""
        return {"type": "read", "experiment": experiment}

    # -- stateful event handler -------------------------------------------

    def _handle_event(self, value: dict[str, Any], state: State) -> None:
        """The ONE stateful op.

        Write-only event types (``lap``/``seed``/``read``) fold best laps into
        State; ``read`` additionally lazily seeds an empty experiment. None of
        them materialise a served snapshot — there is no persistent view. The
        ``get_request`` type is the read path: it reads State in-context and
        delivers the transient payload back to the waiting HTTP handler. The
        ``seed_gate`` / ``mark_seeded`` types read/write the boot-seed ``seeded``
        flag under ``boot_seed.GATE_KEY`` in-context, gating the proactive boot
        seed.
        """
        experiment = str(value.get("experiment") or "")
        event_type = value.get("type")

        if event_type == "get_request":
            # On-demand read round-trip from the HTTP thread. Read State for this
            # experiment key IN-CONTEXT and hand the payload back via the bridge,
            # correlated by req_id. The GET path never seeds (empty -> empty board).
            req_id = str(value.get("req_id") or "")
            if not req_id:
                return
            payload = state.get(experiment) if experiment else None
            # Cheap one-line stat for the in-context read (counts only, no dump).
            groups = (
                {k: v for k, v in payload.items() if isinstance(v, dict)}
                if isinstance(payload, dict)
                else {}
            )
            logger.info(
                "state.get(experiment=%s): %d tracks, %d car groups, %d driver entries",
                experiment or "<active>",
                len(groups),
                sum(len(c) for c in groups.values()),
                sum(len(d) for c in groups.values() for d in c.values()),
            )
            self._pending.deliver(req_id, payload)
            return

        if event_type == "seed_gate":
            # Boot-seed gate read (boot_seed.run_boot_seed). Read the State-native
            # seeded flag for GATE_KEY IN-CONTEXT and hand the bool back via the
            # bridge, correlated by req_id. Placed before the empty-experiment
            # guard because gate events carry experiment=GATE_KEY, not a real one.
            req_id = str(value.get("req_id") or "")
            if not req_id:
                return
            seeded = bool(state.get(_GATE_FLAG))
            logger.info("seed_gate -> seeded=%s", seeded)
            self._pending.deliver(req_id, {"seeded": seeded})
            return

        if event_type == "mark_seeded":
            # Boot-seed gate write: set the State-native flag in-context so a later
            # boot (retained store) skips the lake query. No reply.
            state.set(_GATE_FLAG, True)
            logger.info("mark_seeded -> State flag set")
            return

        if not experiment:
            return

        if event_type == "lap":
            payload = state.get(experiment)
            payload, changed = fold_lap(
                payload,
                str(value.get("track") or ""),
                str(value.get("carModel") or ""),
                str(value.get("driver") or ""),
                int(value.get("best_ms") or 0),
                environment=str(value.get("environment") or ""),
            )
            if changed:
                state.set(experiment, payload)
            return

        if event_type == "seed":
            # Proactive boot seed (boot_seed.run_boot_seed). The carried rows are
            # the lakehouse bests for THIS experiment; fold them in-context — the
            # actual RocksDB write — only when State is empty (idempotent: never
            # clobber a populated experiment). Write-only: no served snapshot.
            payload = state.get(experiment)
            if not payload:
                payload, changed = self._fold_seed_rows(value, payload)
                if changed:
                    state.set(experiment, payload)
            return

        if event_type == "read":
            # Write-only trigger: lazily seed a genuinely empty experiment from
            # the lakehouse in-context. Builds no served snapshot.
            payload = state.get(experiment)
            if not payload:
                payload, seeded = seed_experiment_payload(
                    self._settings, experiment, payload
                )
                if seeded:
                    state.set(experiment, payload)

    @staticmethod
    def _fold_seed_rows(
        value: dict[str, Any], payload: dict[str, Any] | None
    ) -> tuple[dict[str, Any], bool]:
        """Fold a boot ``type="seed"`` message's carried rows into *payload*.

        Each row is ``{track, carModel, driver, best_lap_ms}``; INT_MAX/invalid
        values are dropped by ``fold_lap``. Returns ``(payload, changed)``.
        """
        environment = str(value.get("environment") or "")
        result = dict(payload) if payload else {}
        any_changed = False
        for row in value.get("rows") or []:
            if not isinstance(row, dict):
                continue
            result, changed = fold_lap(
                result,
                str(row.get("track") or ""),
                str(row.get("carModel") or ""),
                str(row.get("driver") or ""),
                int(row.get("best_lap_ms") or 0),
                environment=environment,
            )
            any_changed = any_changed or changed
        return result, any_changed

    # -- HTTP-thread read round-trip --------------------------------------

    def active_experiment(self) -> str:
        """Resolve the current/active experiment from the enrichment caches.

        This is the live session/config-derived experiment signal (a tiny string,
        NOT a best-laps payload) used when ``GET /best-laps`` omits ``experiment``.
        Returns ``""`` when no experiment is resolvable yet.
        """
        return str(self._enrichment.enrich({}).get("experiment") or "")

    def produce_get_request(self, experiment: str, req_id: str) -> None:
        """Produce one ``{"type":"get_request",experiment,req_id}`` event.

        Called from the HTTP thread. Keyed by *experiment* so it co-partitions
        onto the same RocksDB instance the stateful SDF reads in-context. Uses the
        same events-topic serializers / a short-lived producer as the seed path.
        """
        self._produce_event_message(
            experiment,
            {"type": "get_request", "experiment": experiment, "req_id": req_id},
        )

    # -- boot seed ---------------------------------------------------------

    def run_boot_seed(self) -> bool:
        """Proactively seed State once at boot (call on a worker thread).

        Delegates to :func:`best_laps_cache.boot_seed.run_boot_seed`, supplying the
        ``_produce_event_message`` closure (serialises each event against the events
        Topic and produces it via a short-lived producer) and the
        :class:`~best_laps_cache.request_bridge.PendingRequests` bridge (for the
        State-native seeded-flag gate round-trip). The stateful SDF then folds seed
        messages in-context (the only place a RocksDB write may happen) and answers
        the gate read. ``run_boot_seed`` is itself non-raising; the wrapper is a
        belt-and-braces guard so the boot thread can never crash startup.
        """
        try:
            return run_boot_seed(
                self._settings, self._produce_event_message, self._pending
            )
        except Exception:  # noqa: BLE001 — boot seed must never crash startup
            logger.exception("boot seed failed; lazy in-context seed remains")
            return False

    def _produce_event_message(self, key: str, message: dict[str, Any]) -> None:
        """Serialise + produce one event message to the events topic.

        Shared by the boot seed (``type="seed"`` / ``seed_gate`` / ``mark_seeded``)
        and the on-demand read round-trip (``type="get_request"``). Uses the same
        ``self._events_topic``
        serializers the stateful SDF consumes with, so the message is
        wire-identical to a normal event. A fresh producer is opened and flushed
        per call; the SDF owns the consumer side, so this only needs the
        lightweight producer path.
        """
        topic = self._events_topic
        kafka_msg = topic.serialize(key=key, value=message)
        with self._app.get_producer() as producer:
            producer.produce(
                topic=topic.name,
                key=kafka_msg.key,
                value=kafka_msg.value,
            )

    # -- run ---------------------------------------------------------------

    def run(self) -> None:
        """Blocking ``app.run()`` — MUST be called on the main thread (it
        installs SIGINT/SIGTERM handlers via ``signal.signal``)."""
        logger.info(
            "best-laps pipeline starting (inputs=%s, %s, %s; events=%s)",
            self._settings.raw_topic,
            self._settings.session_topic,
            self._settings.config_topic,
            EVENTS_TOPIC,
        )
        self._app.run()
