"""HTTP API for the best-laps cache — direct in-memory mirror read.

``GET /best-laps`` returns ``text/csv`` in the exact shape the Lakehouse
``/query`` returns for the leaderboard's best-laps scan (columns incl.
``driver`` and ``iBestTime``), so the dashboard can keep its existing
``/leaderboard`` → ``GET /best-laps`` path unchanged. ``?format=json`` returns
the Lakehouse-``/query``-compatible row envelope.

Data source: the :class:`~best_laps_cache.mirror.BestLapsMirror` in-memory
mirror, updated by the SDF thread on every successful fold. The HTTP thread
reads directly — no Kafka round-trip, no per-request timeout.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .mirror import BestLapsMirror
from .settings import Settings
from .state_model import filter_rows, to_rows

if TYPE_CHECKING:
    from .pipeline import Pipeline

logger = logging.getLogger(__name__)

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
    """Flatten a mirror *payload* for *experiment*, filter, map to ``iBestTime``
    column shape, sorted fastest-first within group.

    *payload* is the nested dict from the mirror (or ``None`` when the mirror has
    no entry for this experiment yet). Experiment is intrinsic to the mirror key.
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


def create_app(
    pipeline: Pipeline,
    mirror: BestLapsMirror,
    settings: Settings,
) -> FastAPI:
    app = FastAPI(title="best-laps-cache", version="0.4.0")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "active_experiment": pipeline.active_experiment() or None,
            "materialized_experiments": mirror.experiments(),
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
        if not target:
            # No experiment resolvable yet — empty board (200), never an error.
            logger.info("GET /best-laps: no active experiment resolved -> empty board")
            rows: list[dict[str, Any]] = []
        else:
            payload = mirror.get(target)
            rows = build_best_laps_table(target, payload, track=track, car_model=carModel)

        if driver:
            rows = [r for r in rows if r["driver"] == driver]

        logger.info(
            "GET /best-laps experiment=%s -> %d rows (format=%s)",
            target,
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
                    "as_of_epoch": time.time(),
                }
            )
        return PlainTextResponse(_to_csv(rows), media_type="text/csv")

    return app
