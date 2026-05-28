"""Real-mode `/leaderboard/live-positions` assembly.

LOCAL_DEV_MODE stays in `live_positions_sim`. This module powers the
cloud path: query QuixLake for historical best laps, look up the live
driver from `live_telemetry`, and assemble the same `LivePositionEntry`
shape the frontend already consumes.

Public entry point: `build_live_positions(mongo)`. Raises
`LeaderboardError` on configuration/upstream failures so the route
layer can map to a 500 with a useful `detail`.

Why a separate module from `leaderboard.py`:

* `leaderboard.py` stays a thin router that picks sim-vs-real and
  returns the response. Everything that touches the lake, Mongo or the
  consumer state lives here.
* Keeps the LOCAL_DEV_MODE path (`live_positions_sim`) byte-identical
  in observable behaviour — the sim module is never imported by real
  mode and vice versa.

Why no nested SQL / CTE: QuixLake silently returns 0 rows for queries
that use `WITH …`. The per-driver best-lap reduction happens in Python
(`_reduce_to_per_driver_best`).
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from pymongo.database import Database
from quixlake import QuixLakeClient

from .. import live_telemetry
from ..settings import get_settings
from . import live_positions_sim as sim

logger = logging.getLogger(__name__)


# Single-level GROUP BY; one row per (track, carModel, experiment, driver,
# session_id, lap). The per-driver-best reduction is finished in Python.
# `lap_time_ms = MAX(timestamp_ms) - MIN(timestamp_ms)` is the same
# technique the old `/best-laps` route used — works for sessions that
# never completed `iLastTime`.
_BEST_LAPS_SQL = """
SELECT
  track,
  carModel,
  experiment,
  driver,
  session_id,
  lap,
  MAX(timestamp_ms) - MIN(timestamp_ms) AS lap_time_ms
FROM ac_telemetry
GROUP BY track, carModel, experiment, driver, session_id, lap
ORDER BY track, carModel, experiment, driver, lap_time_ms ASC
""".strip()


# Historicals per (track, car, experiment) group. The UI collapses to 8
# rows by default (rank 1 + 7 around the active driver) and expands to
# the full field on demand, so we ship up to 99 historicals per group
# (+ 1 active = max 100 rows) — enough headroom for any real-world
# driver field without paying for an unbounded payload.
_HISTORICAL_CAP_PER_GROUP = 99


class LeaderboardError(RuntimeError):
    """Real-mode failure that the route layer surfaces as HTTP 500."""


# ---------------------------------------------------------------------------
# Driver-name display-case lookup (copied from the old /best-laps route).
# ---------------------------------------------------------------------------


def _fold_driver_name(name: str) -> str:
    """Fold a driver name to a diacritic-insensitive lowercase ASCII key.

    The lake partitions `driver` via `str.lower()`, which preserves
    diacritics (`"Ludvík".lower() == "ludvík"`). In practice users typically
    type driver IDs without diacritics, so a Mongo `"Ludvík"` must match a
    lake `"ludvik"`. NFKD + ASCII fold yields the same key for both.

    Edge case: a name folded to empty (e.g. CJK) keeps its plain
    `.lower()` form so the lookup entry isn't silently dropped.
    """
    if not name:
        return ""
    folded = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    if not folded:
        return name.lower()
    return folded


def _build_driver_name_lookup(mongo: Database[dict[str, Any]]) -> dict[str, str]:
    """`{folded_name: display_name}` map from the Mongo `drivers` collection."""
    lookup: dict[str, str] = {}
    for doc in mongo.drivers.find({}, {"name": 1}):
        name = doc.get("name")
        if isinstance(name, str) and name:
            lookup[_fold_driver_name(name)] = name
    return lookup


# ---------------------------------------------------------------------------
# Lake query + reduction
# ---------------------------------------------------------------------------


def _query_lake(quixlake_url: str, quix_lake_token: str) -> list[dict[str, Any]]:
    """Run the per-lap aggregation against QuixLake and return raw rows.

    NaNs in partition columns are coerced to empty strings — same pattern
    as `telemetry-comparison/main.py` — so downstream `or ""` coalescing
    works without surprises.
    """
    client = QuixLakeClient(base_url=quixlake_url, token=quix_lake_token)
    logger.info("Querying QuixLake for live-positions best laps via QuixLakeClient.")
    df = client.query(_BEST_LAPS_SQL)
    df = df.fillna("")
    rows: list[dict[str, Any]] = df.to_dict("records")
    logger.info("Live-positions lake query returned %d per-lap rows", len(rows))
    return rows


def _reduce_to_per_driver_best(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str, str], tuple[int, int]]:
    """Collapse per-lap rows to `{(track, car, exp, driver): (best_ms, lap)}`.

    Drops each session's highest-lap-number partition — that's the lap
    still in progress when telemetry capture stopped, so its
    `MAX(timestamp_ms) - MIN(timestamp_ms)` is a partial duration and
    not a real lap time. Same logic as the prior `/best-laps` route.
    """
    max_lap_per_session: dict[str, int] = {}
    for row in rows:
        session_id = row.get("session_id") or ""
        try:
            lap_num = int(row.get("lap") or 0)
        except (TypeError, ValueError):
            continue
        if lap_num > max_lap_per_session.get(session_id, -1):
            max_lap_per_session[session_id] = lap_num

    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]] = {}
    for row in rows:
        session_id = row.get("session_id") or ""
        try:
            lap_num = int(row.get("lap") or 0)
        except (TypeError, ValueError):
            continue
        # Drop the in-progress lap (highest lap in this session).
        if lap_num >= max_lap_per_session.get(session_id, -1):
            continue

        raw_lap_ms = row.get("lap_time_ms")
        if raw_lap_ms is None or raw_lap_ms == "":
            continue
        try:
            lap_time_ms = int(float(raw_lap_ms))
        except (TypeError, ValueError):
            continue
        if lap_time_ms <= 0:
            continue

        key = (
            str(row.get("track") or ""),
            str(row.get("carModel") or ""),
            str(row.get("experiment") or ""),
            str(row.get("driver") or ""),
        )
        if not key[0] or not key[1] or not key[2] or not key[3]:
            continue
        existing = best_per_group.get(key)
        if existing is None or lap_time_ms < existing[0]:
            best_per_group[key] = (lap_time_ms, lap_num)
    return best_per_group


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _historical_row(
    track: str,
    car: str,
    experiment: str,
    display_driver: str,
    best_lap_ms: int,
    best_lap_number: int,
    current_lap_time_ms: int,
) -> dict[str, object]:
    return {
        "track": track,
        "car": car,
        "experiment": experiment,
        "driver": display_driver,
        "best_lap_ms": best_lap_ms,
        "best_lap_number": best_lap_number,
        "is_active": False,
        "current_lap": None,
        "current_lap_time_ms": current_lap_time_ms,
        "rank": 0,
    }


def _active_row(
    track: str,
    car: str,
    experiment: str,
    display_driver: str,
    best_lap_ms: int | None,
    best_lap_number: int | None,
    current_lap: int,
    current_lap_time_ms: int,
) -> dict[str, object]:
    return {
        "track": track,
        "car": car,
        "experiment": experiment,
        "driver": display_driver,
        "best_lap_ms": best_lap_ms,
        "best_lap_number": best_lap_number,
        "is_active": True,
        "current_lap": current_lap,
        "current_lap_time_ms": max(0, int(current_lap_time_ms)),
        "rank": 0,
    }


def _best_for_active(
    historical_best_ms: int | None, i_last_time_ms: int | None
) -> int | None:
    """Pick the minimum of lake-historical and live `iLastTime`.

    `iLastTime` is AC's most-recently-completed lap; if the driver just
    set a new personal best mid-session and that lap isn't in the lake
    yet, it should still show up in the leaderboard.
    """
    candidates = [v for v in (historical_best_ms, i_last_time_ms) if v and v > 0]
    if not candidates:
        return None
    return min(candidates)


def _group_keys(
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
) -> set[tuple[str, str, str]]:
    return {(t, c, e) for (t, c, e, _d) in best_per_group}


def _historicals_for_group(
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
    driver_name_lookup: dict[str, str],
    track: str,
    car: str,
    experiment: str,
    norm_pos: float,
) -> list[dict[str, object]]:
    """4-row cap of fastest historicals for one (track, car, experiment).

    Each historical's `current_lap_time_ms` is the ghost estimate at the
    live driver's `normalizedCarPosition`. Splits are equal — see
    `sim.EQUAL_SPLITS`.
    """
    candidates: list[tuple[int, int, str]] = []
    for (t, c, e, raw_driver), (best_ms, lap_num) in best_per_group.items():
        if t == track and c == car and e == experiment:
            candidates.append((best_ms, lap_num, raw_driver))
    candidates.sort(key=lambda x: x[0])
    candidates = candidates[:_HISTORICAL_CAP_PER_GROUP]

    rows: list[dict[str, object]] = []
    for best_ms, lap_num, raw_driver in candidates:
        display_driver = driver_name_lookup.get(
            _fold_driver_name(raw_driver), raw_driver
        )
        # Each historical's ghost lap is scaled to *its own* best_ms so
        # the elapsed numbers stay on the same time scale as its lap.
        h_sector, h_elapsed, h_start, h_end, _ = sim.sector_window_from_norm_pos(
            norm_pos, lap_ms=best_ms
        )
        ghost_ms = sim.ghost_time_for_splits(
            sim.EQUAL_SPLITS,
            best_lap_ms=best_ms,
            active_sector=h_sector,
            elapsed_ms=h_elapsed,
            sector_start_ms=h_start,
            sector_end_ms=h_end,
        )
        rows.append(
            _historical_row(
                track=track,
                car=car,
                experiment=experiment,
                display_driver=display_driver,
                best_lap_ms=best_ms,
                best_lap_number=lap_num,
                current_lap_time_ms=ghost_ms,
            )
        )
    return rows


def _build_group_rows(
    track: str,
    car: str,
    experiment: str,
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
    driver_name_lookup: dict[str, str],
    active: dict[str, Any] | None,
) -> list[dict[str, object]]:
    """Assemble the (≤5) rows for one (track, car, experiment) group.

    Returns an already-ranked list of `LivePositionEntry`-shaped dicts.
    """
    norm_pos: float
    if active and active.get("track") == track and active.get("car") == car:
        try:
            norm_pos = float(active.get("normalized_position") or 0.0)
        except (TypeError, ValueError):
            norm_pos = 0.0
    else:
        norm_pos = 0.0

    rows = _historicals_for_group(
        best_per_group, driver_name_lookup, track, car, experiment, norm_pos
    )

    # Inject the active row only when its experiment matches this group.
    if (
        active
        and active.get("track") == track
        and active.get("car") == car
        and (active.get("experiment") or "") == experiment
    ):
        raw_driver = str(active.get("driver") or "")
        display_driver = driver_name_lookup.get(
            _fold_driver_name(raw_driver), raw_driver
        )
        # `iBestTime` is the freshest session best from AC. `iLastTime`
        # might be smaller if the just-finished lap just set a PB; lake
        # best may be from an earlier session.
        active_historical_key = (track, car, experiment, raw_driver)
        # Try the lake first under the raw driver name; if that misses,
        # try the folded name (lake partitions are lowercased + diacritic
        # -preserving, but real data sometimes ships ASCII-folded).
        lake_best: int | None = None
        lake_lap: int | None = None
        if active_historical_key in best_per_group:
            best_ms, lap_num = best_per_group[active_historical_key]
            lake_best, lake_lap = best_ms, lap_num
        else:
            folded = _fold_driver_name(raw_driver)
            for (t, c, e, d), (b_ms, l_num) in best_per_group.items():
                if (
                    t == track
                    and c == car
                    and e == experiment
                    and _fold_driver_name(d) == folded
                ):
                    lake_best, lake_lap = b_ms, l_num
                    break

        i_last_time = active.get("best_lap_ms_session")
        try:
            i_last_int: int | None = int(i_last_time) if i_last_time else None
        except (TypeError, ValueError):
            i_last_int = None
        active_best = _best_for_active(lake_best, i_last_int)
        # `best_lap_number` only makes sense if our best came from the
        # lake; the live `iLastTime` doesn't tell us which lap it was.
        active_best_lap_number = (
            lake_lap if active_best is not None and active_best == lake_best else None
        )

        try:
            current_lap_time_ms = int(active.get("current_lap_time_ms") or 0)
        except (TypeError, ValueError):
            current_lap_time_ms = 0
        try:
            current_lap = int(active.get("current_lap") or 1)
        except (TypeError, ValueError):
            current_lap = 1

        rows.append(
            _active_row(
                track=track,
                car=car,
                experiment=experiment,
                display_driver=display_driver,
                best_lap_ms=active_best,
                best_lap_number=active_best_lap_number,
                current_lap=current_lap,
                current_lap_time_ms=current_lap_time_ms,
            )
        )

    sim.rank_group(rows)
    return rows


def _solo_active_group(
    active: dict[str, Any],
    driver_name_lookup: dict[str, str],
) -> list[dict[str, object]]:
    """Emit a 1-row group for a live driver whose (track, car, exp) has
    no historical entries in the lake yet. Rank 1, no historicals.
    """
    raw_driver = str(active.get("driver") or "")
    display_driver = driver_name_lookup.get(
        _fold_driver_name(raw_driver), raw_driver
    )
    i_last_time = active.get("best_lap_ms_session")
    try:
        i_last_int: int | None = int(i_last_time) if i_last_time else None
    except (TypeError, ValueError):
        i_last_int = None
    try:
        current_lap_time_ms = int(active.get("current_lap_time_ms") or 0)
    except (TypeError, ValueError):
        current_lap_time_ms = 0
    try:
        current_lap = int(active.get("current_lap") or 1)
    except (TypeError, ValueError):
        current_lap = 1

    row = _active_row(
        track=str(active.get("track") or ""),
        car=str(active.get("car") or ""),
        experiment=str(active.get("experiment") or ""),
        display_driver=display_driver,
        best_lap_ms=i_last_int,
        best_lap_number=None,
        current_lap=current_lap,
        current_lap_time_ms=current_lap_time_ms,
    )
    row["rank"] = 1
    return [row]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_live_positions(
    mongo: Database[dict[str, Any]],
) -> list[dict[str, object]]:
    """Build the real-mode `/live-positions` payload.

    Raises `LeaderboardError` when QuixLake credentials are missing or
    the lake query fails. A missing-or-stale live driver is *not* an
    error — the endpoint serves a historical-only payload (200 OK).
    """
    settings = get_settings()
    if not settings.quixlake_url or not settings.quix_lake_token:
        raise LeaderboardError("QuixLake credentials missing")

    # Read from the in-process historicals cache instead of hitting the lake
    # on every poll. The cache is refreshed by `live_telemetry`'s session
    # handler (once per AC session start) and at consumer warm-up; the per-
    # request path here is now lake-free in the common case.
    best_per_group = live_telemetry.get_historicals_cache()
    if best_per_group is None:
        # Cold start: no refresh has run yet (consumer thread might be
        # disabled or hasn't reached its warm-up). Do one synchronous
        # refresh so the first poll after backend boot still serves data.
        # `refresh_historicals_cache` swallows its own exceptions, so we
        # need to re-check afterwards and surface upstream failures as
        # `LeaderboardError` only when the cache is still empty.
        try:
            live_telemetry.refresh_historicals_cache(
                settings.quixlake_url, settings.quix_lake_token
            )
        except Exception as e:  # defensive — refresh should already swallow
            logger.exception("QuixLake query failed")
            raise LeaderboardError(str(e)) from e
        best_per_group = live_telemetry.get_historicals_cache()
        if best_per_group is None:
            # Refresh failed (logged inside refresh_historicals_cache) and
            # we have nothing to serve. Treat as upstream failure.
            raise LeaderboardError("QuixLake query failed; see backend logs")
    driver_name_lookup = _build_driver_name_lookup(mongo)

    try:
        active = live_telemetry.get_active_driver()
    except Exception:
        # `get_active_driver()` is in-process and shouldn't throw, but
        # if it does (e.g. corrupt state), degrade to historical-only.
        logger.exception("get_active_driver() raised; serving historical-only")
        active = None

    out: list[dict[str, object]] = []
    historical_keys = _group_keys(best_per_group)
    for track, car, experiment in sorted(historical_keys):
        out.extend(
            _build_group_rows(
                track,
                car,
                experiment,
                best_per_group,
                driver_name_lookup,
                active,
            )
        )

    # Edge case: live driver is racing in a (track, car, experiment) that
    # has no historicals at all. Spec: emit a 1-row solo group, rank 1.
    if active:
        active_key = (
            str(active.get("track") or ""),
            str(active.get("car") or ""),
            str(active.get("experiment") or ""),
        )
        if (
            active_key[0]
            and active_key[1]
            and active_key[2]
            and active_key not in historical_keys
        ):
            out.extend(_solo_active_group(active, driver_name_lookup))

    return out
