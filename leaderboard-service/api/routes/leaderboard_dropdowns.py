"""Leaderboard dropdown + best-laps endpoints.

Two endpoints drive the cascading dropdown UX on the Leaderboard tab.
Both fire on user navigation only (open tab, pick experiment/track/car).
The tree endpoint queries QuixLake directly; `/best-laps` serves from
`live_telemetry`'s shared best-laps TTL cache when the requested
(track, car, experiment) is a known group, and otherwise from a small
module-local keyed TTL cache with stale-on-error (dashboard pattern —
spec: dev-planning/leaderboard-bestlaps-gates).

Routes:

* `GET /api/v1/leaderboard/experiment-tree`
    → `{"LeaderBoard": {"ks_nurburgring": ["bmw_1m", ...], ...}, ...}`

* `GET /api/v1/leaderboard/best-laps?experiment={exp}&track={track}&car={car}`
    → `[{"driver": "Ludvík", "best_lap_ms": 119054}, …]`

The tree endpoint replaces the older per-step probe pipeline
(`/experiments` + `/experiment-options`) with a single lake query whose
`experiment IN (...)` predicate prunes lake partitions across every
candidate at once. Mongo `tests` remains the source-of-truth for
*which* experiments to look up; the lake decides which of those
actually have telemetry rows (and which tracks/cars).

Driver-name display-case folding is reused from `leaderboard_real`
(`_build_driver_name_lookup`, `_fold_driver_name`) so the UI shows the
Mongo display case even though the lake stores `str.lower()` keys.

The existing `/live-positions` endpoint stays untouched — Live Sector
Comparison still uses it. This module owns the lake-driven Best Laps
table only.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.database import Database

from .. import live_telemetry
from ..auth import read_permission
from ..lakehouse_client import LakehouseClient
from ..mongo import get_mongo
from ..settings import get_settings
from .leaderboard_real import (
    LeaderboardError,
    _build_driver_name_lookup,
    _fold_driver_name,
    _format_sql_string,
    _query_best_laps_min,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# Cap the `IN (...)` list to keep statement size bounded. In practice
# the Mongo `tests.experiment_id` set is single-digit, so this is purely
# defensive — operators with very large fleets would hit a soft warning,
# not a hard failure.
_MAX_EXPERIMENTS_IN_TREE = 200


# ---------------------------------------------------------------------------
# SQL builders — single-level GROUP BY (QuixLake silently returns 0 rows for
# CTE / WITH queries; see `feedback_quixlake_no_cte`).
# ---------------------------------------------------------------------------


def _build_experiment_tree_sql(experiments: list[str]) -> str:
    """`(experiment, track, carModel)` triples for every experiment in `experiments`.

    Single SQL with `experiment IN (...)` so QuixLake prunes the lake's
    `experiment` partitions across every candidate in one shot — same
    partition-pruning behaviour that worked for the old single-experiment
    `WHERE experiment = ...` queries, extended to multiple values.

    Each value is single-quote-escaped via `_format_sql_string`.
    """
    lake_table = get_settings().lake_table
    quoted = ", ".join(f"'{_format_sql_string(e)}'" for e in experiments)
    return (
        "SELECT experiment, track, carModel "
        f"FROM {lake_table} "
        f"WHERE experiment IN ({quoted}) "
        "GROUP BY experiment, track, carModel "
        "ORDER BY experiment, track, carModel"
    )


# Module-local TTL cache for combos OUTSIDE `live_telemetry._known_groups()`
# (a user browsing an arbitrary dropdown combination). Shape:
# `{(experiment, track, car): (refreshed_monotonic, {folded_driver: ms})}`.
# Shares `BEST_LAPS_TTL_SECONDS` with the live cache (spec §8 open question
# resolved as "shared"); per-key stale-on-error mirrors the dashboard
# pattern (`telemetry-dashboard/main.py:302-324`).
_combo_cache_lock = threading.Lock()
_combo_cache: dict[tuple[str, str, str], tuple[float, dict[str, int]]] = {}


def _query_combo_best_laps(
    experiment: str, track: str, car: str
) -> dict[str, int]:
    """Query A (3-filter variant) for one dropdown combo, folded.

    `environment` is unknown for arbitrary dropdown combos — pre-existing
    contract, the documented exception to the 4-partition-filter rule.
    Uses `_query_best_laps_min` (aggregated `MIN(...) GROUP BY driver`
    with automatic raw-scan fallback) and folds the raw driver keys.
    """
    settings = get_settings()
    if not settings.lakehouse_query_url or not settings.lakehouse_query_token:
        raise LeaderboardError("Lakehouse credentials missing")
    per_driver_raw = _query_best_laps_min(
        settings.lakehouse_query_url,
        settings.lakehouse_query_token,
        track=track,
        car=car,
        experiment=experiment,
        environment=None,
    )
    folded: dict[str, int] = {}
    for raw_driver, best_ms in per_driver_raw.items():
        key = _fold_driver_name(raw_driver)
        prev = folded.get(key)
        if prev is None or best_ms < prev:
            folded[key] = best_ms
    return folded


# ---------------------------------------------------------------------------
# Lake query helpers
# ---------------------------------------------------------------------------


def _get_lake_client() -> LakehouseClient:
    """Build a `LakehouseClient` from settings, or raise `LeaderboardError`.

    Mirrors the `build_live_positions` precondition check so the route
    layer keeps a uniform 500-with-detail surface.
    """
    settings = get_settings()
    if not settings.lakehouse_query_url or not settings.lakehouse_query_token:
        raise LeaderboardError("Lakehouse credentials missing")
    return LakehouseClient(
        base_url=settings.lakehouse_query_url, token=settings.lakehouse_query_token
    )


def _candidate_experiments(mongo: Database[dict[str, Any]]) -> list[str]:
    """Distinct, non-empty `experiment_id` values from Mongo `tests`, sorted."""
    raw = mongo.tests.distinct("experiment_id")
    return sorted({str(v).strip() for v in raw if isinstance(v, str) and v.strip()})


def _reduce_tree_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    """Fold `(experiment, track, carModel)` rows into the nested dict shape.

    Skips rows missing any of the three fields. Outer + inner dicts are
    sorted lexicographically and each leaf `list[car]` is sorted +
    deduplicated for defensiveness — the lake's `ORDER BY` already
    delivers sorted rows, but the reduce stage is cheap.
    """
    tree: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        experiment = str(row.get("experiment") or "").strip()
        track = str(row.get("track") or "").strip()
        car = str(row.get("carModel") or "").strip()
        if not experiment or not track or not car:
            continue
        tree.setdefault(experiment, {}).setdefault(track, set()).add(car)
    return {
        exp: {trk: sorted(cars) for trk, cars in sorted(by_track.items())}
        for exp, by_track in sorted(tree.items())
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/experiment-tree")
async def get_experiment_tree(
    _auth: None = Depends(read_permission),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
) -> dict[str, dict[str, list[str]]]:
    """Return the `{experiment: {track: [car, ...]}}` tree filtered to
    experiments that have data in the configured lake table.

    Two-phase build:
      1. Mongo `tests` gives the candidate `(experiment, track, car)`
         combinations — Test Manager writes these on every session-link.
      2. One probe per experiment against the configured `LAKE_TABLE`
         (default `ac_telemetry`). Parallel; per-probe timeout 5 s.
         Experiments whose probe returns no rows are dropped from the
         result.

    The probe is `SELECT experiment FROM <table> WHERE experiment = '…'
    GROUP BY experiment` — same form that responds instantly today.
    Mongo runs once and is fast; the parallel probes typically complete
    in <2 s total even with a half-dozen experiments.
    """
    try:
        # Phase 1: Mongo aggregate.
        tree: dict[str, dict[str, set[str]]] = {}
        for doc in mongo.tests.find(
            {}, {"experiment_id": 1, "sessions": 1, "_id": 0}
        ):
            experiment = str(doc.get("experiment_id") or "").strip()
            if not experiment:
                continue
            for session in doc.get("sessions") or []:
                if not isinstance(session, dict):
                    continue
                track = str(session.get("track") or "").strip()
                car = str(session.get("car_model") or "").strip()
                if not track or not car:
                    continue
                tree.setdefault(experiment, {}).setdefault(track, set()).add(car)

        candidates = sorted(tree.keys())
        if not candidates:
            logger.info("experiment-tree: mongo=0 experiments")
            return {}

        # Phase 2: parallel per-experiment lake probes against LAKE_TABLE.
        client = _get_lake_client()
        lake_table = get_settings().lake_table

        def _probe(exp: str) -> bool:
            sql = (
                f"SELECT experiment FROM {lake_table} "
                f"WHERE experiment = '{_format_sql_string(exp)}' "
                "GROUP BY experiment"
            )
            try:
                df = client.query(sql)
                return not df.empty
            except Exception:
                logger.warning("experiment-tree probe failed for %r", exp, exc_info=False)
                return False

        probe_results = await asyncio.gather(
            *(asyncio.to_thread(_probe, exp) for exp in candidates),
            return_exceptions=False,
        )
        has_lake_data = {exp for exp, ok in zip(candidates, probe_results) if ok}

        result = {
            exp: {trk: sorted(cars) for trk, cars in sorted(by_track.items())}
            for exp, by_track in sorted(tree.items())
            if exp in has_lake_data
        }
        logger.info(
            "experiment-tree: mongo=%d → lake(%s)=%d",
            len(candidates),
            lake_table,
            len(result),
        )
        return result
    except LeaderboardError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        logger.exception("GET /leaderboard/experiment-tree failed")
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
    ascending by `best_lap_ms` (contract unchanged —
    `leaderboard-ui/lib/api/leaderboard.ts:79-92`). Driver names are
    mapped from the lake's folded-lowercase form back to the Mongo
    display case.

    Source order (spec §6.5):
      1. `live_telemetry`'s shared best-laps cache — entries matching
         (track, car, experiment) across any environment, MIN-merged per
         folded driver. No lake call.
      2. Module-local combo cache (fresh within `BEST_LAPS_TTL_SECONDS`).
      3. Lake (Query A, 3-filter variant); on failure, serve the stale
         combo-cache entry if one exists.
    """
    logger.info(
        "best-laps request: experiment=%r track=%r car=%r",
        experiment,
        track,
        car,
    )

    best_by_folded: dict[str, int] | None = None

    # 1. Shared cache hit: any known group for this (track, car, exp).
    shared = live_telemetry.get_best_laps_cache()
    if shared:
        merged: dict[str, int] = {}
        hit = False
        for (g_track, g_car, g_exp, _env), group_rows in shared.items():
            if g_track == track and g_car == car and g_exp == experiment:
                hit = True
                for folded, best_ms in group_rows.items():
                    prev = merged.get(folded)
                    if prev is None or best_ms < prev:
                        merged[folded] = best_ms
        if hit:
            logger.info("best-laps served from shared live cache (no lake call)")
            best_by_folded = merged

    # 2./3. Module-local keyed TTL cache with stale-on-error.
    if best_by_folded is None:
        combo_key = (experiment, track, car)
        ttl = get_settings().best_laps_ttl_seconds
        now = time.monotonic()
        with _combo_cache_lock:
            entry = _combo_cache.get(combo_key)
        if entry is not None and now - entry[0] < ttl:
            logger.info("best-laps served from combo cache (no lake call)")
            best_by_folded = entry[1]
        else:
            try:
                best_by_folded = await asyncio.to_thread(
                    _query_combo_best_laps, experiment, track, car
                )
                with _combo_cache_lock:
                    _combo_cache[combo_key] = (now, best_by_folded)
            except LeaderboardError as e:
                raise HTTPException(status_code=500, detail=str(e)) from e
            except Exception as e:
                if entry is not None:
                    logger.warning(
                        "best-laps lake query failed (%s); serving stale combo "
                        "cache entry",
                        e,
                    )
                    best_by_folded = entry[1]
                else:
                    logger.exception("GET /leaderboard/best-laps failed")
                    raise HTTPException(status_code=500, detail=str(e)) from e

    driver_name_lookup = _build_driver_name_lookup(mongo)

    out: list[dict[str, Any]] = []
    for folded, best_ms in best_by_folded.items():
        display = driver_name_lookup.get(folded, folded)
        out.append({"driver": display, "best_lap_ms": best_ms})
    out.sort(key=lambda r: r["best_lap_ms"])
    sample = ", ".join(f"{r['driver']}={r['best_lap_ms']}" for r in out[:3])
    logger.info(
        "best-laps response: %d driver(s)%s",
        len(out),
        f" — {sample}{'…' if len(out) > 3 else ''}" if sample else "",
    )
    return out
