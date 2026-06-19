"""HTTP API for the best-laps cache — an on-demand State read round-trip.

``GET /best-laps`` returns ``text/csv`` in the exact shape the Lakehouse
``/query`` returns for the leaderboard's best-laps scan (columns incl.
``driver`` and ``iBestTime``), so the dashboard can keep its existing
``/leaderboard`` → ``GET /best-laps`` path unchanged. ``?format=json`` returns
the Lakehouse-``/query``-compatible row envelope.

Data source: QuixStreams **native State (RocksDB)**, read **per request,
in-context**. State is reachable only inside the stateful SDF processing context
for a message's key, so the HTTP thread round-trips through the SDF: it produces
a synthetic ``{"type":"get_request",experiment,req_id}`` event keyed by the
target experiment, the SDF reads ``state.get(experiment)`` in-context and hands
the payload back via the :class:`~best_laps_cache.request_bridge.PendingRequests`
bridge (correlated by ``req_id``), and the handler builds the table from that
**transient** payload and discards it at request end. No best-laps payload
persists in RAM between requests, and there is no SQL anywhere.

On a round-trip timeout the endpoint returns an **empty 200 board** (same shape)
so the dashboard never errors — it just renders an empty leaderboard until State
warms up.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .request_bridge import PendingRequests
from .settings import Settings
from .state_model import filter_rows, to_rows

if TYPE_CHECKING:
    from .pipeline import Pipeline

logger = logging.getLogger(__name__)

# Round-trip wait budget for the in-context State read (seconds).
_READ_TIMEOUT_S = 3.0

# Column order the leaderboard's raw-scan SQL selects. `iBestTime` is kept
# verbatim (mapped from `best_lap_ms`) so the shape is column-compatible with
# the lake query the dashboard's path historically consumed.
_CSV_COLUMNS = ["environment", "experiment", "track", "carModel", "driver", "iBestTime"]


def build_best_laps_table(
    experiment: str,
    payload: dict[str, Any] | None,
    *,
    track: str | None = None,
    car_model: str | None = None,
) -> list[dict[str, Any]]:
    """The reusable GET-wrapper core: flatten a **transient** State *payload* for
    *experiment*, filter by *track* + *car_model*, map to the ``iBestTime``
    column shape, sorted fastest-first within group.

    *payload* is the nested dict just read from State in-context (or ``None`` when
    State was empty / the read timed out). Experiment is intrinsic to the State
    key, so it selects which payload was read — it is not a within-payload filter.
    """
    flattened = to_rows(experiment, payload)
    filtered = filter_rows(flattened, track=track, car_model=car_model)
    rows = [
        {
            "environment": r.get("environment", ""),
            "experiment": r.get("experiment", ""),
            "track": r.get("track", ""),
            "carModel": r.get("carModel", ""),
            "driver": r.get("driver", ""),
            "iBestTime": int(r.get("best_lap_ms", 0)),
        }
        for r in filtered
    ]
    rows.sort(key=lambda r: (r["track"], r["carModel"], r["iBestTime"]))
    return rows


def _to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def read_experiment_payload(
    pipeline: Pipeline,
    pending: PendingRequests,
    experiment: str,
    *,
    timeout: float = _READ_TIMEOUT_S,
) -> tuple[dict[str, Any] | None, bool]:
    """Round-trip through the SDF to read State for *experiment*, in-context.

    Opens a ``req_id`` slot, produces a ``get_request`` event keyed by
    *experiment*, waits on the slot's Event up to *timeout*, then removes the
    slot. Returns ``(payload, delivered)`` — *payload* is the transient State dict
    (or ``None`` on empty/timeout). The slot is always cleaned up.
    """
    req_id = pending.open()
    try:
        pipeline.produce_get_request(experiment, req_id)
    except Exception:  # noqa: BLE001 — a broker hiccup must not 500 the dashboard
        logger.exception("failed to produce get_request for experiment=%s", experiment)
        pending.close(req_id)
        return None, False
    delivered, payload = pending.wait(req_id, timeout)
    return payload, delivered


def create_app(
    pipeline: Pipeline,
    pending: PendingRequests,
    settings: Settings,
) -> FastAPI:
    app = FastAPI(title="best-laps-cache", version="0.3.0")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "active_experiment": pipeline.active_experiment() or None,
            "in_flight_requests": pending.pending_count(),
        }

    @app.get("/best-laps")
    def best_laps(
        environment: str | None = Query(None),  # accepted, not a filter (single env)
        experiment: str | None = Query(None),
        track: str | None = Query(None),
        carModel: str | None = Query(None),  # noqa: N803 — public query-param name
        driver: str | None = Query(None),  # accepted for back-compat; not filtered
        format: str = Query("csv"),  # noqa: A002 — public query-param name
    ):
        # Target experiment: the explicit param, else the live active experiment.
        target = experiment or pipeline.active_experiment()
        as_of: float | None = None
        if not target:
            # No experiment resolvable yet — empty board (200), never an error.
            logger.info("GET /best-laps: no active experiment resolved -> empty board")
            rows: list[dict[str, Any]] = []
        else:
            # Per-request, in-context State read. `payload` is held in RAM only for
            # this request; it goes out of scope when the handler returns.
            payload, delivered = read_experiment_payload(pipeline, pending, target)
            if not delivered:
                logger.warning(
                    "GET /best-laps: State read round-trip timed out for "
                    "experiment=%s -> empty board (200)",
                    target,
                )
                rows = []
            else:
                rows = build_best_laps_table(
                    target, payload, track=track, car_model=carModel
                )
                as_of = time.time()
            # `payload` deliberately dropped here — nothing persists between requests.

        # `driver` is accepted for URL back-compat with the old endpoint but the
        # board returns all drivers for the track+car (the dashboard overlays the
        # "me" row client-side); filter here only if explicitly requested.
        if driver:
            rows = [r for r in rows if r["driver"] == driver]
        applied = {
            k: v
            for k, v in {
                "experiment": target,
                "track": track,
                "carModel": carModel,
                "driver": driver,
            }.items()
            if v is not None
        } or "none"
        logger.info(
            "GET /best-laps filters=%s -> %d rows (format=%s)",
            applied,
            len(rows),
            format.lower(),
        )
        if format.lower() == "json":
            return JSONResponse(
                {
                    "table": settings.lake_table,
                    "columns": _CSV_COLUMNS,
                    "rows": rows,
                    "row_count": len(rows),
                    "source": "best-laps-cache",
                    "as_of_epoch": as_of if as_of is not None else time.time(),
                }
            )
        return PlainTextResponse(_to_csv(rows), media_type="text/csv")

    return app
