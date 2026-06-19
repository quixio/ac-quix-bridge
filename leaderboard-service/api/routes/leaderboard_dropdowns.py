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

The tree endpoint is lake-first: it folds
`partition_index.enumerate_groups()` (the lake's own partition
enumeration, TTL-cached) into the nested dict shape. Mongo plays no
role in the tree anymore — an empty Mongo `tests` collection no longer
blanks the dropdowns, and only combos that actually have lake data
appear (the enumeration guarantees it).

Driver names are sourced from the lake's folded-lowercase `driver` key
and Title-Cased for display (e.g. `"tomas neubauer"` ->
`"Tomas Neubauer"`). Mongo plays no role — the lake value (DCM-enriched
at write time) is the only source.

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

from .. import live_telemetry, partition_index
from ..auth import read_permission
from ..settings import get_settings
from .leaderboard_real import (
    LeaderboardError,
    _fold_driver_name,
    _query_best_laps_min,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


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
# Routes
# ---------------------------------------------------------------------------


@router.get("/experiment-tree")
async def get_experiment_tree(
    _auth: None = Depends(read_permission),
) -> dict[str, dict[str, list[str]]]:
    """Return the `{experiment: {track: [car, ...]}}` tree of combos that
    have telemetry rows in the configured lake table.

    Lake-first: folds `partition_index.enumerate_groups()` — the lake's
    own `(track, car, experiment, environment)` enumeration (TTL-cached,
    metadata-bootstrap + pruned GROUP BY) — into the nested dict shape.
    `environment` is collapsed: the same (experiment, track, car) under
    two environments appears once.

    Mongo is not consulted; an empty Test Manager no longer blanks the
    dropdowns. An empty lake yields `{}` with 200 — never a 500
    (`enumerate_groups` swallows enumeration failures and serves its
    last-good or empty result).
    """
    try:
        groups = await asyncio.to_thread(partition_index.enumerate_groups)
        tree: dict[str, dict[str, set[str]]] = {}
        for track, car, experiment, _environment in groups:
            tree.setdefault(experiment, {}).setdefault(track, set()).add(car)
        result = {
            exp: {trk: sorted(cars) for trk, cars in sorted(by_track.items())}
            for exp, by_track in sorted(tree.items())
        }
        logger.info(
            "experiment-tree: %d experiment(s) from lake enumeration",
            len(result),
        )
        return result
    except Exception as e:
        logger.exception("GET /leaderboard/experiment-tree failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/best-laps")
async def get_best_laps(
    experiment: str = Query(..., min_length=1),
    track: str = Query(..., min_length=1),
    car: str = Query(..., min_length=1),
    _auth: None = Depends(read_permission),
) -> list[dict[str, Any]]:
    """Return per-driver best-lap rows for one (experiment, track, car).

    Shape: `[{"driver": "Ludvík", "best_lap_ms": 119054}, ...]`, sorted
    ascending by `best_lap_ms` (contract unchanged —
    `ui/lib/api/leaderboard.ts:79-92`). Driver names are the lake's
    folded-lowercase form, Title-Cased for display (no Mongo lookup).

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

    out: list[dict[str, Any]] = []
    for folded, best_ms in best_by_folded.items():
        # Title-Case each word of the folded lake key (e.g. "tomas neubauer"
        # -> "Tomas Neubauer"). Only the displayed string is title-cased —
        # the folded key stays the matching key everywhere else. No Mongo.
        display = folded.title()
        out.append({"driver": display, "best_lap_ms": best_ms})
    out.sort(key=lambda r: r["best_lap_ms"])
    sample = ", ".join(f"{r['driver']}={r['best_lap_ms']}" for r in out[:3])
    logger.info(
        "best-laps response: %d driver(s)%s",
        len(out),
        f" — {sample}{'…' if len(out) > 3 else ''}" if sample else "",
    )
    return out
