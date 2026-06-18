"""QuixStreams ``Application`` + ``StreamingDataFrame`` pipeline (State-native).

Durable store: QuixStreams' **native State (RocksDB)** on the ``state:`` volume —
no other database anywhere. State is keyed by ``experiment``; the value is the
nested payload ``{_env, track: {carModel: {folded_driver: {best_lap_ms,
best_lap_number, gate_vector}}}}`` (see
:mod:`leaderboard_service_state.state_model`). Each best lap stores its full
``GATE_COUNT`` gate vector so the gate comparison algorithm runs entirely off
State with zero lake queries on the request path.

Topology (mirrors the cache's three-root SDF under one ``app.run()`` with an
intermediate experiment-keyed events topic):

* **Write branch** — ``ac-telemetry-raw`` -> enrich (track/car/driver/experiment)
  -> **pre-group_by stateful lap accumulator** keyed by the native raw key:
  accumulate ``(normalizedCarPosition, iCurrentTime)`` samples for the in-flight
  lap; on **lap completion** (``completedLaps`` strictly increased, or
  ``iCurrentTime`` reset) reduce the samples to a ``GATE_COUNT`` gate vector via
  the shared :func:`gate_vector_from_samples`, take ``lap_ms = MAX(iCurrentTime)``,
  drop partial laps, and emit a ``{"type":"lap", ...}`` event to the events topic
  keyed by ``experiment``. Clears the accumulator for the next lap.
* **Read-trigger branch** — ``ac-telemetry-session`` + ``ac-telemetry-config`` ->
  feed enrichment caches -> emit ``{"type":"read","experiment":...}``. Write-only:
  it drives the lazy in-context seed of a genuinely empty experiment.
* **Stateful branch** — consumes the events topic (one ``stream_id`` -> one State
  store). ``type="lap"``/``type="seed"`` fold into State; ``type="read"`` lazily
  seeds an empty experiment; ``type="get_request"`` reads State in-context and
  delivers the **transient** payload back to the waiting HTTP handler via the
  :class:`~leaderboard_service_state.request_bridge.PendingRequests` bridge.

Why the events topic (not two ``group_by("experiment")`` branches sharing a
store): a QuixStreams state store is scoped by ``stream_id``, and ``group_by``
derives a new ``stream_id`` per source, so branches from different source topics
land in different stores. Funnelling both producers into one experiment-keyed
events topic gives one stateful SDF, one ``stream_id``, one RocksDB store.

NO persistent materialized view: the leaderboard payload lives in RAM only for the
duration of one ``get_request`` round-trip, then is discarded.
"""

from __future__ import annotations

import logging
from typing import Any

from quixstreams import Application
from quixstreams.state import State

from .boot_seed import run_boot_seed
from .enrichment import Enrichment
from .gate_vector import gate_vector_from_samples
from .request_bridge import PendingRequests
from .seed import seed_experiment_payload
from .settings import Settings
from .state_model import INT_MAX, count_stats, fold_best_lap

logger = logging.getLogger(__name__)

# Lap-completeness threshold (matches leaderboard_real ``_PARTIAL_LAP_MAX_POS``):
# a lap whose samples cover < this fraction of the track is dropped.
_PARTIAL_LAP_MAX_POS = 0.95

# Pre-group_by accumulator store keys (per native raw key).
_LAP_SAMPLES_KEY = "_lap_samples"
_LAP_COMPLETED_KEY = "_lap_completed"
_LAP_LAST_CUR_KEY = "_lap_last_cur"


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
        self._gate_count = settings.gate_count
        self._app = build_application(settings)
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
        session_topic = app.topic(s.session_topic, value_deserializer="json")
        config_topic = app.topic(s.config_topic, value_deserializer="json")
        events_topic = app.topic(
            s.events_topic,
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
            lambda v: bool(v)
            and bool(v["driver"])
            and bool(v["experiment"])
            and bool(v["track"])
            and bool(v["carModel"])
        )
        # Pre-group_by stateful lap accumulator, keyed by the native raw key.
        # Returns a {"type":"lap", ...} dict on lap completion, else None.
        sdf_raw = sdf_raw.apply(self._accumulate_lap, stateful=True)
        sdf_raw = sdf_raw.filter(lambda v: bool(v))
        sdf_raw.to_topic(events_topic, key=lambda v: v["experiment"])

        # -- read-trigger branch: session + config -> events("read") ------
        sdf_session = app.dataframe(session_topic)
        sdf_session = sdf_session.apply(self._resolve_session)
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
        """Enrich one raw tick to the five-key group + lap-progress fields."""
        try:
            i_current = int(value.get("iCurrentTime") or 0)
            completed = int(value.get("completedLaps") or 0)
            norm_pos = float(value.get("normalizedCarPosition") or 0.0)
        except (TypeError, ValueError):
            return {}
        fields = self._enrichment.enrich(value)
        return {
            "experiment": fields["experiment"],
            "environment": fields["environment"],
            "track": fields["track"],
            "carModel": fields["carModel"],
            "driver": fields["driver"],
            "iCurrentTime": i_current,
            "completedLaps": completed,
            "normalizedCarPosition": norm_pos,
        }

    def _accumulate_lap(
        self, value: dict[str, Any], state: State
    ) -> dict[str, Any] | None:
        """Accumulate the in-flight lap's samples; emit a ``lap`` event on
        completion.

        Keyed by the native raw key (this op runs BEFORE the re-key to
        experiment). Lap completion = ``completedLaps`` strictly increased OR
        ``iCurrentTime`` reset (the AC mid-session-restart case). On completion the
        **previous** lap's accumulated samples are reduced to a gate vector and a
        ``{"type":"lap", ...}`` event is returned; the accumulator restarts with
        the current tick. Returns ``None`` on every non-completing tick.
        """
        i_current = int(value["iCurrentTime"])
        completed = int(value["completedLaps"])
        norm_pos = float(value["normalizedCarPosition"])

        prev_completed = state.get(_LAP_COMPLETED_KEY)
        prev_cur = state.get(_LAP_LAST_CUR_KEY)
        samples: list[list[float]] = state.get(_LAP_SAMPLES_KEY) or []

        lap_done = prev_completed is not None and (
            completed > int(prev_completed)
            or (prev_cur is not None and i_current < int(prev_cur))
        )

        emit: dict[str, Any] | None = None
        if lap_done:
            emit = self._finish_lap(value, samples, int(prev_completed))
            samples = []

        # Append the current tick to the (possibly reset) accumulator.
        samples.append([norm_pos, i_current])
        if len(samples) > self._settings.max_lap_samples:
            # Defensive cap on a stuck stream: keep the tail.
            samples = samples[-self._settings.max_lap_samples :]

        state.set(_LAP_SAMPLES_KEY, samples)
        state.set(_LAP_COMPLETED_KEY, completed)
        state.set(_LAP_LAST_CUR_KEY, i_current)
        return emit

    def _finish_lap(
        self,
        value: dict[str, Any],
        samples: list[list[float]],
        lap_number: int,
    ) -> dict[str, Any] | None:
        """Reduce a completed lap's samples to a ``{"type":"lap", ...}`` event.

        Drops empty, partial (``max_pos < _PARTIAL_LAP_MAX_POS``), and
        INT_MAX/non-positive laps. ``lap_ms = MAX(iCurrentTime)`` over the lap
        (matches ``_reduce_lap_table``). ``lap_number`` is the completed lap's
        index (the ``completedLaps`` value before the increment, 1-based here as
        the lap that just finished).
        """
        if not samples:
            return None
        max_pos = max(s[0] for s in samples)
        lap_ms = max(int(s[1]) for s in samples)
        if lap_ms <= 0 or lap_ms >= INT_MAX:
            return None
        if max_pos < _PARTIAL_LAP_MAX_POS:
            return None
        ordered = sorted(((float(p), int(t)) for p, t in samples), key=lambda s: s[1])
        gate_vector = gate_vector_from_samples(ordered, self._gate_count)
        return {
            "type": "lap",
            "experiment": value["experiment"],
            "environment": value["environment"],
            "track": value["track"],
            "carModel": value["carModel"],
            "driver": value["driver"],
            "lap_ms": lap_ms,
            "lap_number": int(lap_number) + 1,
            "gate_vector": gate_vector,
        }

    # -- read-trigger callbacks -------------------------------------------

    def _resolve_session(self, value: dict[str, Any]) -> dict[str, Any]:
        hostname = str(value.get("hostname") or value.get("target_key") or "default")
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

        ``lap``/``seed`` fold best-lap gate records into State (write-only);
        ``read`` lazily seeds an empty experiment; ``get_request`` reads State
        in-context and delivers the **transient** payload to the waiting HTTP
        handler. None of them maintain a persistent view.
        """
        experiment = str(value.get("experiment") or "")
        event_type = value.get("type")

        if event_type == "get_request":
            req_id = str(value.get("req_id") or "")
            if not req_id:
                return
            payload = state.get(experiment) if experiment else None
            # Cheap per-GET state.get stat log: entry counts only, no payload dump.
            tracks, car_groups, drivers = count_stats(payload)
            logger.info(
                "state.get(experiment=%s): %d tracks, %d car groups, %d driver entries",
                experiment or "<none>",
                tracks,
                car_groups,
                drivers,
            )
            self._pending.deliver(req_id, payload)
            return

        if not experiment:
            return

        if event_type == "lap":
            payload = state.get(experiment)
            payload, changed = fold_best_lap(
                payload,
                str(value.get("track") or ""),
                str(value.get("carModel") or ""),
                str(value.get("driver") or ""),
                int(value.get("lap_ms") or 0),
                list(value.get("gate_vector") or []),
                int(value.get("lap_number") or 0),
                environment=str(value.get("environment") or ""),
            )
            if changed:
                state.set(experiment, payload)
            return

        if event_type == "seed":
            payload = state.get(experiment)
            if not payload:
                payload, changed = self._fold_seed_rows(value, payload)
                if changed:
                    state.set(experiment, payload)
            return

        if event_type == "read":
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

        Each row is ``{track, carModel, driver, best_lap_ms, best_lap_number,
        gate_vector}``; INT_MAX/invalid values are dropped by ``fold_best_lap``.
        Returns ``(payload, changed)``.
        """
        environment = str(value.get("environment") or "")
        result = dict(payload) if payload else {}
        any_changed = False
        for row in value.get("rows") or []:
            if not isinstance(row, dict):
                continue
            result, changed = fold_best_lap(
                result,
                str(row.get("track") or ""),
                str(row.get("carModel") or ""),
                str(row.get("driver") or ""),
                int(row.get("best_lap_ms") or 0),
                list(row.get("gate_vector") or []),
                int(row.get("best_lap_number") or 0),
                environment=environment,
            )
            any_changed = any_changed or changed
        return result, any_changed

    # -- HTTP-thread read round-trip --------------------------------------

    def active_experiment(self) -> str:
        """Resolve the current/active experiment from the enrichment caches."""
        return str(self._enrichment.enrich({}).get("experiment") or "")

    def produce_get_request(self, experiment: str, req_id: str) -> None:
        """Produce one ``{"type":"get_request",experiment,req_id}`` event.

        Called from the HTTP thread. Keyed by *experiment* so it co-partitions onto
        the same RocksDB instance the stateful SDF reads in-context.
        """
        self._produce_event_message(
            experiment,
            {"type": "get_request", "experiment": experiment, "req_id": req_id},
        )

    # -- boot seed ---------------------------------------------------------

    def run_boot_seed(self) -> bool:
        """Proactively seed State once at boot (call on a worker thread)."""
        try:
            return run_boot_seed(self._settings, self._produce_event_message)
        except Exception:  # noqa: BLE001 — boot seed must never crash startup
            logger.exception("boot seed failed; lazy in-context seed remains")
            return False

    def _produce_event_message(self, key: str, message: dict[str, Any]) -> None:
        """Serialise + produce one event message to the events topic."""
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
        """Blocking ``app.run()`` — MUST be called on the main thread (it installs
        SIGINT/SIGTERM handlers via ``signal.signal``)."""
        logger.info(
            "leaderboard-state pipeline starting (inputs=%s, %s, %s; events=%s; "
            "gate_count=%d)",
            self._settings.raw_topic,
            self._settings.session_topic,
            self._settings.config_topic,
            self._settings.events_topic,
            self._gate_count,
        )
        self._app.run()
