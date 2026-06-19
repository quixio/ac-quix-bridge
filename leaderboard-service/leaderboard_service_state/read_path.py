"""Per-request, in-context State read for the leaderboard HTTP path.

There is **NO persistent materialized view** (read-path-no-ram.md). To serve a
``GET /live-positions`` the HTTP thread round-trips through the SDF: produce a
``get_request`` event keyed by experiment, the stateful SDF reads
``state.get(experiment)`` in-context and delivers the transient payload back via
the :class:`~leaderboard_service_state.request_bridge.PendingRequests` bridge
(correlated by ``req_id`` + a :class:`threading.Event` + timeout). The handler
runs the gate comparison algorithm on that transient payload and discards it at
request end. Nothing leaderboard-shaped persists in RAM between requests.

This module is the read facade the rewired
``api/routes/leaderboard_real.build_live_positions`` calls instead of issuing lake
queries: :func:`read_historicals_and_standings` returns, for one experiment,

* ``historicals``: ``{(track, car, experiment): {folded_driver: _HistoricalEntry}}``
  — the exact structure ``gate_math.compute_last_gate_state`` consumes, unchanged;
* ``best_laps``: ``{(track, car, experiment, environment): {folded_driver: ms}}``
  — the per-driver scalar bests the standings assembly merges the active row into.

Both are built from the transient payload, then the payload goes out of scope.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .request_bridge import PendingRequests
from .state_model import (
    BEST_LAP_MS,
    BEST_LAP_NUMBER,
    GATE_VECTOR,
    environment_of,
    to_historicals,
)

if TYPE_CHECKING:
    from .pipeline import Pipeline

logger = logging.getLogger(__name__)

# Round-trip wait budget for the in-context State read (seconds).
READ_TIMEOUT_S = 3.0


def read_experiment_payload(
    pipeline: Pipeline,
    pending: PendingRequests,
    experiment: str,
    *,
    timeout: float = READ_TIMEOUT_S,
) -> tuple[dict[str, Any] | None, bool]:
    """Round-trip through the SDF to read State for *experiment*, in-context.

    Opens a ``req_id`` slot, produces a ``get_request`` event keyed by *experiment*,
    waits on the slot's Event up to *timeout*, then removes the slot. Returns
    ``(payload, delivered)`` — *payload* is the transient State dict (or ``None`` on
    empty/timeout). The slot is always cleaned up.
    """
    req_id = pending.open()
    try:
        pipeline.produce_get_request(experiment, req_id)
    except Exception:  # noqa: BLE001 — a broker hiccup must not 500 the board
        logger.exception("failed to produce get_request for experiment=%s", experiment)
        pending.close(req_id)
        return None, False
    delivered, payload = pending.wait(req_id, timeout)
    return payload, delivered


def historicals_from_payload(
    experiment: str,
    payload: dict[str, Any] | None,
    historical_entry_cls: type,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Adapt a transient State *payload* to the ``_HistoricalEntry`` historicals.

    Returns ``{(track, car, experiment): {folded_driver: _HistoricalEntry}}``.
    *historical_entry_cls* is ``live_telemetry._HistoricalEntry`` (passed in to
    avoid importing the heavy module here). The records carry exactly the entry's
    fields, so this is a thin construction with no transformation.
    """
    grouped = to_historicals(experiment, payload)
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for group_key, driver_map in grouped.items():
        entries: dict[str, Any] = {}
        for folded_driver, record in driver_map.items():
            entries[folded_driver] = historical_entry_cls(
                best_lap_ms=int(record[BEST_LAP_MS]),
                best_lap_number=int(record[BEST_LAP_NUMBER]),
                gate_vector=list(record[GATE_VECTOR]),
            )
        out[group_key] = entries
    return out


def best_laps_from_payload(
    experiment: str,
    payload: dict[str, Any] | None,
) -> dict[tuple[str, str, str, str], dict[str, int]]:
    """Adapt a transient State *payload* to the best-laps cache shape.

    Returns ``{(track, car, experiment, environment): {folded_driver: best_ms}}`` —
    the same key shape ``leaderboard_real._build_group_rows`` expects from the old
    lake-backed ``best_laps_cache``.
    """
    environment = environment_of(payload)
    grouped = to_historicals(experiment, payload)
    out: dict[tuple[str, str, str, str], dict[str, int]] = {}
    for (track, car, exp), driver_map in grouped.items():
        key = (track, car, exp, environment)
        out[key] = {
            folded_driver: int(record[BEST_LAP_MS])
            for folded_driver, record in driver_map.items()
        }
    return out
