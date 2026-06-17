"""HTTP API for the best-laps cache.

``GET /best-laps`` returns ``text/csv`` in the exact shape the Lakehouse
``/query`` returns for the leaderboard's best-laps scan (columns incl.
``driver`` and ``iBestTime``), so a consumer can swap its Lakehouse query URL
for this endpoint with zero parsing change (O5). A ``?format=json`` variant
returns the Lakehouse-``/query``-compatible row envelope (spec §7.1).

The API reads the in-memory :class:`BestLapsStore` mirror only — never the
Lakehouse, never QuixStreams State directly — so a slow lake or a busy
consumer never delays a response.
"""

from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse

from .settings import Settings
from .store import BestLapsStore

# Column order the leaderboard's raw-scan SQL selects, plus the partition
# columns. `iBestTime` is kept verbatim (not `best_lap_ms`) so the swap is
# column-name compatible with the lake query the consumer replaces.
_CSV_COLUMNS = ["environment", "experiment", "track", "carModel", "driver", "iBestTime"]


def _rows_for(store: BestLapsStore, **filters: str | None) -> list[dict[str, Any]]:
    values = store.query(
        environment=filters.get("environment"),
        experiment=filters.get("experiment"),
        track=filters.get("track"),
        car_model=filters.get("carModel"),
        driver=filters.get("driver"),
    )
    rows: list[dict[str, Any]] = []
    for v in values:
        rows.append(
            {
                "environment": v.get("environment", ""),
                "experiment": v.get("experiment", ""),
                "track": v.get("track", ""),
                "carModel": v.get("carModel", ""),
                "driver": v.get("driver", ""),
                "iBestTime": int(v.get("best_lap_ms", 0)),
            }
        )
    # Deterministic ordering: fastest first within stable group ordering.
    rows.sort(key=lambda r: (r["track"], r["carModel"], r["iBestTime"]))
    return rows


def _to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def create_app(store: BestLapsStore, settings: Settings) -> FastAPI:
    app = FastAPI(title="best-laps-cache", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "cached_keys": len(store)}

    @app.get("/best-laps")
    def best_laps(
        environment: str | None = Query(None),
        experiment: str | None = Query(None),
        track: str | None = Query(None),
        carModel: str | None = Query(None),  # noqa: N803 — public query-param name
        driver: str | None = Query(None),
        format: str = Query("csv"),  # noqa: A002 — public query-param name
    ):
        rows = _rows_for(
            store,
            environment=environment,
            experiment=experiment,
            track=track,
            carModel=carModel,
            driver=driver,
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
