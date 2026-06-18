"""HTTP API for the best-laps cache — a thin wrapper over the State-derived view.

``GET /best-laps`` returns ``text/csv`` in the exact shape the Lakehouse
``/query`` returns for the leaderboard's best-laps scan (columns incl.
``driver`` and ``iBestTime``), so the dashboard can keep its existing
``/leaderboard`` → ``GET /best-laps`` path unchanged. ``?format=json`` returns
the Lakehouse-``/query``-compatible row envelope.

Data source: the **materialized current view** (``materialized.py``) — a small
per-experiment snapshot the stateful SDF read branch publishes from QuixStreams
State on every session/config trigger and new-best lap. The endpoint reads no
database and no QuixStreams State directly (State is reachable only inside the
processing context); it does the minimum work: look up the active experiment's
rows, filter by ``track`` + ``carModel``, serialize. No SQL anywhere.

The endpoint is a thin shell around :func:`build_best_laps_table`, the single
reusable cache-service function (same flatten+filter core the SDF view uses),
per the GET-wrapper contract.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .materialized import MaterializedView
from .settings import Settings
from .state_model import filter_rows

logger = logging.getLogger(__name__)

# Column order the leaderboard's raw-scan SQL selects. `iBestTime` is kept
# verbatim (mapped from `best_lap_ms`) so the shape is column-compatible with
# the lake query the dashboard's path historically consumed.
_CSV_COLUMNS = ["environment", "experiment", "track", "carModel", "driver", "iBestTime"]


def build_best_laps_table(
    view: MaterializedView,
    *,
    experiment: str | None = None,
    track: str | None = None,
    car_model: str | None = None,
) -> tuple[list[dict[str, Any]], float | None]:
    """The reusable GET-wrapper core: materialized rows for *experiment*
    (active when ``None``), filtered by *track* + *car_model*, mapped to the
    ``iBestTime`` column shape, sorted fastest-first within group.

    Returns ``(rows, as_of_epoch)``. Experiment is intrinsic to the State key,
    so it selects which payload to read — it is not a within-payload filter.
    """
    materialized_rows, as_of = view.get_rows(experiment)
    filtered = filter_rows(materialized_rows, track=track, car_model=car_model)
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
    return rows, as_of


def _to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def create_app(view: MaterializedView, settings: Settings) -> FastAPI:
    app = FastAPI(title="best-laps-cache", version="0.2.0")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "active_experiment": view.active_experiment(),
            "materialized_experiments": len(view.experiments()),
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
        rows, as_of = build_best_laps_table(
            view, experiment=experiment, track=track, car_model=carModel
        )
        # `driver` is accepted for URL back-compat with the old endpoint but the
        # board returns all drivers for the track+car (the dashboard overlays
        # the "me" row client-side); filter here only if explicitly requested.
        if driver:
            rows = [r for r in rows if r["driver"] == driver]
        as_of_age = f"{time.time() - as_of:.0f}s" if as_of is not None else "n/a"
        applied = {
            k: v
            for k, v in {
                "experiment": experiment,
                "track": track,
                "carModel": carModel,
                "driver": driver,
            }.items()
            if v is not None
        } or "none"
        logger.info(
            "GET /best-laps filters=%s -> %d rows (format=%s, as-of age=%s)",
            applied,
            len(rows),
            format.lower(),
            as_of_age,
        )
        if format.lower() == "json":
            return JSONResponse(
                {
                    "table": settings.lake_table,
                    "columns": _CSV_COLUMNS,
                    "rows": rows,
                    "row_count": len(rows),
                    "source": "best-laps-cache",
                    "as_of_epoch": time.time(),
                }
            )
        return PlainTextResponse(_to_csv(rows), media_type="text/csv")

    return app
