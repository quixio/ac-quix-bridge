"""Step 1.5 leaderboard dropdown + best-laps endpoints.

Three thin endpoints that drive the cascading dropdown UX on the
Leaderboard tab. None of these are hot-loop polled — they fire on user
navigation only (open tab, pick experiment, pick track/car) — so each
call queries QuixLake directly. No caches, no in-process state.

Routes:

* `GET /api/v1/leaderboard/experiments`
    → `["LeaderBoard", "shakedown", …]`

* `GET /api/v1/leaderboard/experiment-options?experiment={experiment}`
    → `{"tracks": ["ks_nurburgring", …], "cars": ["bmw_1m", …]}`

* `GET /api/v1/leaderboard/best-laps?experiment={exp}&track={track}&car={car}`
    → `[{"driver": "Ludvík", "best_lap_ms": 119054}, …]`

Driver-name display-case folding is reused from `leaderboard_real`
(`_build_driver_name_lookup`, `_fold_driver_name`) so the UI shows the
Mongo display case even though the lake stores `str.lower()` keys.

The existing `/live-positions` endpoint stays untouched — Live Sector
Comparison still uses it. This module owns the lake-driven Best Laps
table only.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database
from quixlake import QuixLakeClient

from ..auth import read_permission
from ..mongo import get_mongo
from ..settings import get_settings
from .leaderboard_real import (
    LeaderboardError,
    _build_driver_name_lookup,
    _fold_driver_name,
    _format_sql_string,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# ---------------------------------------------------------------------------
# Pydantic-free response shapes (FastAPI infers from return type annotation).
# ---------------------------------------------------------------------------


class BestLapRow(dict[str, Any]):
    """Documentation shim — actual payload is `{"driver": str, "best_lap_ms": int}`."""


# ---------------------------------------------------------------------------
# SQL builders — single-level GROUP BY (QuixLake silently returns 0 rows for
# CTE / WITH queries; see `feedback_quixlake_no_cte`).
# ---------------------------------------------------------------------------


def _build_experiments_sql() -> str:
    """Distinct non-empty experiments across the lake, sorted ascending."""
    return (
        "SELECT DISTINCT experiment FROM ac_telemetry "
        "WHERE experiment IS NOT NULL AND experiment != '' "
        "ORDER BY experiment"
    )


def _build_tracks_for_experiment_sql(experiment: str) -> str:
    """Distinct non-null tracks for one experiment."""
    return (
        "SELECT DISTINCT track FROM ac_telemetry "
        f"WHERE experiment = '{_format_sql_string(experiment)}' "
        "AND track IS NOT NULL "
        "ORDER BY track"
    )


def _build_cars_for_experiment_sql(experiment: str) -> str:
    """Distinct non-null car models for one experiment."""
    return (
        "SELECT DISTINCT carModel FROM ac_telemetry "
        f"WHERE experiment = '{_format_sql_string(experiment)}' "
        "AND carModel IS NOT NULL "
        "ORDER BY carModel"
    )


def _build_best_laps_for_combo_sql(experiment: str, track: str, car: str) -> str:
    """Per-driver best lap for one (experiment, track, car), sorted ascending.

    Note: no `environment` filter. The spec explicitly removed environment
    from the dropdowns — if the same (experiment, track, car) tuple has
    rows in multiple environments, MIN across them all is the desired
    behaviour for this step.
    """
    return (
        "SELECT driver, "
        "MIN(iBestTime) FILTER (WHERE iBestTime > 0) AS best_lap_ms "
        "FROM ac_telemetry "
        f"WHERE experiment = '{_format_sql_string(experiment)}' "
        f"AND track = '{_format_sql_string(track)}' "
        f"AND carModel = '{_format_sql_string(car)}' "
        "GROUP BY driver "
        "ORDER BY best_lap_ms ASC"
    )


# ---------------------------------------------------------------------------
# Lake query helpers
# ---------------------------------------------------------------------------


def _get_lake_client() -> QuixLakeClient:
    """Build a `QuixLakeClient` from settings, or raise `LeaderboardError`.

    Mirrors the `build_live_positions` precondition check so the route
    layer keeps a uniform 500-with-detail surface.
    """
    settings = get_settings()
    if not settings.quixlake_url or not settings.quix_lake_token:
        raise LeaderboardError("QuixLake credentials missing")
    return QuixLakeClient(
        base_url=settings.quixlake_url, token=settings.quix_lake_token
    )


def _query_distinct_strings(client: QuixLakeClient, sql: str, column: str) -> list[str]:
    """Run a SELECT-DISTINCT query and return the single column as a list."""
    logger.info("dropdown SQL: %s", sql)
    df = client.query(sql)
    df = df.fillna("")
    rows: list[dict[str, Any]] = df.to_dict("records")
    out: list[str] = []
    for row in rows:
        value = row.get(column)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        out.append(text)
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/experiments", response_model=list[str])
async def get_experiments(_auth: None = Depends(read_permission)) -> list[str]:
    """Return all distinct experiments in the lake, sorted ascending.

    Empty list when the lake is empty. 500 with `detail` on lake failure
    or missing credentials.
    """
    try:
        client = _get_lake_client()
        return _query_distinct_strings(client, _build_experiments_sql(), "experiment")
    except LeaderboardError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("GET /leaderboard/experiments failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/experiment-options")
async def get_experiment_options(
    experiment: str = Query(..., min_length=1),
    _auth: None = Depends(read_permission),
) -> dict[str, list[str]]:
    """Return `{"tracks": [...], "cars": [...]}` for a given experiment.

    Two single-column distinct queries — cheaper for the QuixLake side
    than one cross-product distinct, and the response shape stays flat
    for the frontend.
    """
    try:
        client = _get_lake_client()
        tracks = _query_distinct_strings(
            client, _build_tracks_for_experiment_sql(experiment), "track"
        )
        cars = _query_distinct_strings(
            client, _build_cars_for_experiment_sql(experiment), "carModel"
        )
        return {"tracks": tracks, "cars": cars}
    except LeaderboardError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("GET /leaderboard/experiment-options failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/best-laps")
async def get_best_laps(
    experiment: str = Query(..., min_length=1),
    track: str = Query(..., min_length=1),
    car: str = Query(..., min_length=1),
    _auth: None = Depends(read_permission),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
) -> list[dict[str, Any]]:
    """Return per-driver best-lap rows for one (experiment, track, car).

    Shape: `[{"driver": "Ludvík", "best_lap_ms": 119054}, ...]`, sorted
    ascending by `best_lap_ms`. Driver names are mapped from the lake's
    folded-lowercase form back to the Mongo display case.
    """
    try:
        client = _get_lake_client()
        sql = _build_best_laps_for_combo_sql(experiment, track, car)
        logger.info("best-laps SQL: %s", sql)
        df = client.query(sql)
        df = df.fillna("")
        rows: list[dict[str, Any]] = df.to_dict("records")
    except LeaderboardError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("GET /leaderboard/best-laps failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    driver_name_lookup = _build_driver_name_lookup(mongo)

    out: list[dict[str, Any]] = []
    for row in rows:
        raw_driver = str(row.get("driver") or "").strip()
        if not raw_driver:
            continue
        raw_best = row.get("best_lap_ms")
        if raw_best is None or raw_best == "":
            continue
        try:
            best_ms = int(float(raw_best))
        except (TypeError, ValueError):
            continue
        if best_ms <= 0:
            continue
        folded = _fold_driver_name(raw_driver)
        display = driver_name_lookup.get(folded, raw_driver)
        out.append({"driver": display, "best_lap_ms": best_ms})

    # Defensive re-sort: the lake's ORDER BY does the heavy lifting, but
    # rows that fail the coerce above are skipped and don't disturb order.
    out.sort(key=lambda r: r["best_lap_ms"])
    return out
