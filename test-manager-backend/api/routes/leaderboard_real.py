"""Real-mode `/leaderboard/live-positions` assembly.

LOCAL_DEV_MODE stays in `live_positions_sim`. This module powers the
cloud path: query QuixLake for per-driver best laps, look up the live
driver from `live_telemetry`, and assemble the `LivePositionEntry`
shape the frontend already consumes.

Public entry point: `build_live_positions(mongo)`. Raises
`LeaderboardError` on configuration/upstream failures so the route
layer can map to a 500 with a useful `detail`.

Step 1 scope (this file): Best Laps table only. The Live Sector
Comparison column on the left is still rendered by the frontend but
every row's `last_gate_*` fields are emitted as `None` because the
gate-vector reducer is dormant until Step 2 lands. See
`docs/architecture-leaderboard-live-positions.md` for the trace.

Why a separate module from `leaderboard.py`:

* `leaderboard.py` stays a thin router that picks sim-vs-real and
  returns the response. Everything that touches the lake, Mongo or the
  consumer state lives here.
* Keeps the LOCAL_DEV_MODE path (`live_positions_sim`) byte-identical
  in observable behaviour — the sim module is never imported by real
  mode and vice versa.

Why no nested SQL / CTE: QuixLake silently returns 0 rows for queries
that use `WITH …` (see `feedback_quixlake_no_cte`). The Step 1 query
is a single-level `GROUP BY driver` with the `iBestTime` aggregation
inside the SELECT.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any, Literal

from pymongo.database import Database
from quixlake import QuixLakeClient

from .. import live_telemetry
from ..live_telemetry import GATE_COUNT, _HistoricalEntry
from ..settings import get_settings
from . import live_positions_sim as sim

logger = logging.getLogger(__name__)


# Historicals per (track, car, experiment, environment) group. The UI
# collapses to 8 rows by default (rank 1 + 7 around the active driver)
# and expands to the full field on demand, so we ship up to 99 historicals
# per group (+ 1 active = max 100 rows) — enough headroom for any
# real-world driver field without paying for an unbounded payload.
_HISTORICAL_CAP_PER_GROUP = 99


# TODO Step 2: re-enable for sector comparison.
# Lap-completeness threshold the legacy gate-vectors reducer used to
# distinguish a sparse-sample real lap from a quit/crash. Unused on the
# Step 1 path; kept for the Step 2 re-enable.
_PARTIAL_LAP_MAX_POS = 0.95


# TODO Step 2: re-enable for sector comparison.
# The legacy per-lap aggregation SQL. Unused on the Step 1 path.
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
# Lake query: Step 1 per-driver best lap (uses AC's own `iBestTime`).
# ---------------------------------------------------------------------------


def _format_sql_string(value: str) -> str:
    """Single-quote-escape a string for inline use in a SQL literal.

    QuixLake's HTTP `/query` endpoint takes a raw SQL string — no
    parameterised queries are exposed via `QuixLakeClient.query()`. We
    inline the WHERE-clause values and escape single quotes by doubling
    them (ANSI SQL convention; DuckDB, Postgres, and ClickHouse all
    accept this form).
    """
    return value.replace("'", "''")


def _build_best_laps_sql(
    track: str, car: str, experiment: str, environment: str
) -> str:
    """Return the per-driver best-lap SQL for one (track, car, exp, env) group.

    Uses AC's `iBestTime` (already a per-driver best lap in milliseconds;
    >0 means a completed lap exists). The `FILTER (WHERE iBestTime > 0)`
    drops rows whose iBestTime is still 0/NULL because the driver hasn't
    set a flying lap yet. `MIN(...)` over the filtered rows yields the
    fastest completed lap for that driver in the lake partition.

    The four-way WHERE filter exactly matches the lake's Hive partitions
    (`environment`, `track`, `carModel`, `experiment`). Forgetting the
    `environment` partition is the regression this query fixes.
    """
    return (
        "SELECT driver, "
        "MIN(iBestTime) FILTER (WHERE iBestTime > 0) AS best_lap_ms "
        "FROM ac_telemetry "
        f"WHERE environment = '{_format_sql_string(environment)}' "
        f"AND track = '{_format_sql_string(track)}' "
        f"AND carModel = '{_format_sql_string(car)}' "
        f"AND experiment = '{_format_sql_string(experiment)}' "
        "GROUP BY driver "
        "ORDER BY best_lap_ms ASC"
    )


def _query_best_laps(
    quixlake_url: str,
    quix_lake_token: str,
    *,
    track: str,
    car: str,
    experiment: str,
    environment: str,
) -> dict[str, int]:
    """Run the Step 1 per-driver best-lap query for one group.

    Returns `{folded_driver: best_lap_ms}`. An empty group (no flying
    laps yet for any driver) returns an empty dict — distinct from an
    upstream failure, which raises (caller catches and logs).

    NaNs in the `driver` column are coerced to empty strings before
    folding so we never produce a `""` cache key from a partial row.
    Rows whose `best_lap_ms` cannot be coerced to a positive int are
    dropped silently — defensive against `FILTER` returning NULL when no
    rows match the predicate for a given driver (DuckDB returns NULL
    rather than dropping the group).
    """
    sql = _build_best_laps_sql(track, car, experiment, environment)
    logger.info(
        "best-laps SQL: %s",
        sql,
    )
    client = QuixLakeClient(base_url=quixlake_url, token=quix_lake_token)
    df = client.query(sql)
    df = df.fillna("")
    rows: list[dict[str, Any]] = df.to_dict("records")

    per_driver: dict[str, int] = {}
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
        per_driver[_fold_driver_name(raw_driver)] = best_ms
    return per_driver


# ---------------------------------------------------------------------------
# TODO Step 2: re-enable for sector comparison.
#
# The functions below — `_query_lake`, `_reduce_to_per_driver_best`,
# `_build_gate_samples_sql`, `_query_gate_samples`, `_reduce_to_gate_vectors`,
# `ghost_ms_at_position`, `_latest_crossed_gate`, `compute_last_gate_state` —
# are dormant on the Step 1 path. They are kept in place (NOT deleted) so the
# Step 2 patch can re-attach them without reconstructing the reducer.
# Step 2 will:
#   * call `_query_lake` again from `refresh_best_laps_cache`,
#   * extend the cache value to carry `_HistoricalEntry` alongside the
#     scalar best_lap_ms,
#   * re-wire `_build_group_rows` to plumb `last_gate_*` triples back onto
#     the active row,
#   * restore frontend colour cues.
# Until then every call site below has been removed from the hot path.
# ---------------------------------------------------------------------------


def _query_lake(quixlake_url: str, quix_lake_token: str) -> list[dict[str, Any]]:
    """[TODO Step 2] Legacy per-lap aggregation against QuixLake.

    Returns raw per-lap rows for the historicals query. Unused on the
    Step 1 hot path; kept so the Step 2 re-enable can import-resolve.
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
    """[TODO Step 2] Collapse per-lap rows to `{(track, car, exp, driver):
    (best_ms, lap)}`."""
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]] = {}
    for row in rows:
        try:
            lap_num = int(row.get("lap") or 0)
        except (TypeError, ValueError):
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


def _build_gate_samples_sql(
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
) -> str:
    """[TODO Step 2] WHERE-clause-laden SQL for the gate-samples query."""
    clauses: list[str] = []
    for (track, car, experiment, driver), (_best_ms, lap_num) in best_per_group.items():
        if not (track and car and experiment and driver and lap_num):
            continue
        clauses.append(
            "(track='{t}' AND carModel='{c}' AND experiment='{e}' "
            "AND driver='{d}' AND lap={lap})".format(
                t=_format_sql_string(track),
                c=_format_sql_string(car),
                e=_format_sql_string(experiment),
                d=_format_sql_string(driver),
                lap=int(lap_num),
            )
        )
    where = " OR ".join(clauses)
    return (
        "SELECT track, carModel, experiment, driver, session_id, lap, "
        "normalizedCarPosition, timestamp_ms "
        f"FROM ac_telemetry WHERE ({where}) "
        "AND normalizedCarPosition IS NOT NULL "
        "ORDER BY track, carModel, experiment, driver, session_id, lap, "
        "timestamp_ms"
    )


def _query_gate_samples(
    quixlake_url: str,
    quix_lake_token: str,
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
) -> list[dict[str, Any]]:
    """[TODO Step 2] Fetch position samples for the best laps in `best_per_group`."""
    sql = _build_gate_samples_sql(best_per_group)
    if "OR" not in sql and "AND lap=" not in sql:
        return []
    client = QuixLakeClient(base_url=quixlake_url, token=quix_lake_token)
    logger.info(
        "Querying QuixLake for gate-samples (%d best-lap rows)", len(best_per_group)
    )
    df = client.query(sql)
    df = df.fillna("")
    rows: list[dict[str, Any]] = df.to_dict("records")
    logger.info("Gate-samples query returned %d position rows", len(rows))
    return rows


def _reduce_to_gate_vectors(
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
    sample_rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, _HistoricalEntry]]:
    """[TODO Step 2] Bucket samples by (track, car, exp, driver, lap), then
    linearly interpolate each gate's cumulative time from the two samples
    that bracket the gate position. Unused on the Step 1 path."""
    buckets: dict[tuple[str, str, str, str, str, int], list[tuple[float, int]]] = {}
    for row in sample_rows:
        try:
            ncp = float(row.get("normalizedCarPosition") or 0.0)
            ts_raw = row.get("timestamp_ms")
            if ts_raw is None or ts_raw == "":
                continue
            ts = int(float(ts_raw))
            lap = int(row.get("lap") or 0)
        except (TypeError, ValueError):
            continue
        key = (
            str(row.get("track") or ""),
            str(row.get("carModel") or ""),
            str(row.get("experiment") or ""),
            str(row.get("driver") or ""),
            str(row.get("session_id") or ""),
            lap,
        )
        if not all(key[:4]) or not lap:
            continue
        buckets.setdefault(key, []).append((ncp, ts))

    out: dict[tuple[str, str, str], dict[str, _HistoricalEntry]] = {}
    for (track, car, experiment, driver), (best_ms, lap_num) in best_per_group.items():
        candidate_keys = sorted(
            k
            for k in buckets
            if k[0] == track
            and k[1] == car
            and k[2] == experiment
            and k[3] == driver
            and k[5] == lap_num
        )
        if not candidate_keys:
            continue
        samples = sorted(buckets[candidate_keys[0]], key=lambda x: x[1])
        if not samples:
            continue
        max_pos = max(s[0] for s in samples)
        if max_pos < _PARTIAL_LAP_MAX_POS:
            continue

        lap_start_ms = samples[0][1]
        gate_vector: list[int] = [0] * GATE_COUNT
        scan_from = 0
        for i in range(GATE_COUNT):
            target = (i + 1) / GATE_COUNT
            interp_ts: float | None = None
            j = scan_from
            n = len(samples)
            while j < n - 1:
                lo_pos, lo_ts = samples[j]
                hi_pos, hi_ts = samples[j + 1]
                if lo_pos <= target <= hi_pos:
                    if hi_pos == lo_pos:
                        interp_ts = float(lo_ts)
                    else:
                        frac = (target - lo_pos) / (hi_pos - lo_pos)
                        interp_ts = lo_ts + frac * (hi_ts - lo_ts)
                    scan_from = j
                    break
                j += 1
            if interp_ts is None:
                nearest = min(samples, key=lambda s, t=target: abs(s[0] - t))
                interp_ts = float(nearest[1])
            gate_vector[i] = max(0, int(interp_ts - lap_start_ms))

        for i in range(1, GATE_COUNT):
            if gate_vector[i] < gate_vector[i - 1]:
                gate_vector[i] = gate_vector[i - 1]

        entry = _HistoricalEntry(
            best_lap_ms=int(gate_vector[GATE_COUNT - 1]) or int(best_ms),
            best_lap_number=int(lap_num),
            gate_vector=gate_vector,
        )
        group_key = (track, car, experiment)
        out.setdefault(group_key, {})[_fold_driver_name(driver)] = entry
    return out


def ghost_ms_at_position(gate_vector: list[int], p: float) -> int:
    """[TODO Step 2] Linear-interpolate a historical's cumulative-ms at
    position `p`. Unused on Step 1."""
    if not gate_vector or len(gate_vector) != GATE_COUNT:
        return 0
    if p <= 0.0:
        return 0
    if p >= 1.0:
        return int(gate_vector[GATE_COUNT - 1])
    pos_idx = int(p * GATE_COUNT)
    if pos_idx >= GATE_COUNT:
        pos_idx = GATE_COUNT - 1
    lo_pos = pos_idx / float(GATE_COUNT)
    hi_pos = (pos_idx + 1) / float(GATE_COUNT)
    lo_t = 0 if pos_idx == 0 else int(gate_vector[pos_idx - 1])
    hi_t = int(gate_vector[pos_idx])
    if hi_pos <= lo_pos:
        return lo_t
    frac = (p - lo_pos) / (hi_pos - lo_pos)
    return int(lo_t + (hi_t - lo_t) * frac)


def _latest_crossed_gate(gate_times_ms: list[int | None]) -> int | None:
    """[TODO Step 2] Highest index with a populated gate time."""
    for i in range(len(gate_times_ms) - 1, -1, -1):
        if gate_times_ms[i] is not None:
            return i
    return None


def compute_last_gate_state(
    active_gate_times: list[int | None],
    historicals: dict[str, _HistoricalEntry] | None,
) -> tuple[
    int | None,
    Literal["ahead", "behind", "neutral"] | None,
    int | None,
]:
    """[TODO Step 2] Compute `(last_gate_index, last_gate_state,
    last_gate_delta_ms)` for the active driver. Unused on Step 1; the
    active row leaves all three fields as `None`."""
    i_star = _latest_crossed_gate(active_gate_times)
    if i_star is None:
        return None, None, None
    active_t = active_gate_times[i_star]
    if active_t is None:
        return None, None, None
    if not historicals:
        return i_star, "neutral", None

    hist_ts: list[int] = []
    for h in historicals.values():
        if len(h.gate_vector) == GATE_COUNT:
            hist_ts.append(int(h.gate_vector[i_star]))
    if not hist_ts:
        return i_star, "neutral", None

    if all(active_t < h for h in hist_ts):
        state: Literal["ahead", "behind", "neutral"] = "ahead"
    elif all(active_t > h for h in hist_ts):
        state = "behind"
    else:
        state = "neutral"
    delta_ms = int(active_t - min(hist_ts))
    return i_star, state, delta_ms


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _historical_row(
    track: str,
    car: str,
    experiment: str,
    display_driver: str,
    best_lap_ms: int,
) -> dict[str, object]:
    """Build a `LivePositionEntry`-shaped dict for a historical driver.

    Step 1: `best_lap_number` is `None` (the new SQL no longer tracks lap
    number), `current_lap_time_ms` is 0 (no ghost interpolation without
    the gate-vector cache), and every `last_gate_*` field stays `None`.
    The frontend's Best Laps table sorts by `best_lap_ms` and ignores
    every other column for historicals, so the zeros are harmless.
    """
    return {
        "track": track,
        "car": car,
        "experiment": experiment,
        "driver": display_driver,
        "best_lap_ms": best_lap_ms,
        "best_lap_number": None,
        "is_active": False,
        "current_lap": None,
        "current_lap_time_ms": 0,
        "rank": 0,
        "last_gate_index": None,
        "last_gate_state": None,
        "last_gate_delta_ms": None,
    }


def _active_row(
    track: str,
    car: str,
    experiment: str,
    display_driver: str,
    best_lap_ms: int | None,
    current_lap: int,
    current_lap_time_ms: int,
) -> dict[str, object]:
    """Build a `LivePositionEntry`-shaped dict for the active driver.

    Step 1: gate-state fields are emitted as `None` until Step 2 restores
    the colour cues. `best_lap_number` is `None` too (the lake-side query
    doesn't compute it any more).
    """
    return {
        "track": track,
        "car": car,
        "experiment": experiment,
        "driver": display_driver,
        "best_lap_ms": best_lap_ms,
        "best_lap_number": None,
        "is_active": True,
        "current_lap": current_lap,
        "current_lap_time_ms": max(0, int(current_lap_time_ms)),
        "rank": 0,
        "last_gate_index": None,
        "last_gate_state": None,
        "last_gate_delta_ms": None,
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


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _build_group_rows(
    track: str,
    car: str,
    experiment: str,
    environment: str,
    best_laps_cache: dict[tuple[str, str, str, str], dict[str, int]],
    driver_name_lookup: dict[str, str],
    active: dict[str, Any] | None,
) -> list[dict[str, object]]:
    """Assemble the rows for one (track, car, experiment, environment)
    group: historicals from the lake cache + the active driver (if it
    matches this group). Returns a ranked list of `LivePositionEntry`-
    shaped dicts.
    """
    group_historicals = best_laps_cache.get((track, car, experiment, environment), {})

    candidates: list[tuple[int, str]] = [
        (best_ms, folded_driver) for folded_driver, best_ms in group_historicals.items()
    ]
    candidates.sort(key=lambda x: x[0])
    candidates = candidates[:_HISTORICAL_CAP_PER_GROUP]

    rows: list[dict[str, object]] = []
    for best_ms, folded_driver in candidates:
        display_driver = driver_name_lookup.get(folded_driver, folded_driver)
        rows.append(
            _historical_row(
                track=track,
                car=car,
                experiment=experiment,
                display_driver=display_driver,
                best_lap_ms=best_ms,
            )
        )

    # Inject the active row only when its (track, car, experiment) matches
    # this group. `environment` is not on the live snapshot — every active
    # driver lives in exactly one DCM experiment config (and therefore one
    # environment), so we can't end up rendering the same active driver in
    # two groups for different environments.
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
        folded = _fold_driver_name(raw_driver)
        lake_best = group_historicals.get(folded)
        i_last_time = active.get("best_lap_ms_session")
        try:
            i_last_int: int | None = int(i_last_time) if i_last_time else None
        except (TypeError, ValueError):
            i_last_int = None
        active_best = _best_for_active(lake_best, i_last_int)

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
    """Emit a 1-row group for a live driver whose (track, car, exp, env)
    has no historical entries in the lake yet. Rank 1, no historicals,
    no gate-state colour (Step 1).
    """
    raw_driver = str(active.get("driver") or "")
    display_driver = driver_name_lookup.get(_fold_driver_name(raw_driver), raw_driver)
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

    Step 1: reads `live_telemetry.get_best_laps_cache()` (per-driver
    best-lap dict, keyed by `(track, car, experiment, environment)`).
    Each historical row carries only `best_lap_ms` + display fields;
    every gate-state column is `None` until Step 2 lands.
    """
    settings = get_settings()
    if not settings.quixlake_url or not settings.quix_lake_token:
        raise LeaderboardError("QuixLake credentials missing")

    # Read from the in-process best-laps cache instead of hitting the
    # lake on every poll. Cache refresh triggers are wired in
    # `live_telemetry`: consumer warm-up, AC session message, DCM config
    # event. The per-request path here is lake-free in the common case.
    best_laps_cache = live_telemetry.get_best_laps_cache()
    if best_laps_cache is None:
        # Cold start: no refresh has run yet (consumer thread might be
        # disabled or hasn't reached its warm-up). Do one synchronous
        # refresh so the first poll after backend boot still serves data.
        try:
            live_telemetry.refresh_best_laps_cache(
                settings.quixlake_url, settings.quix_lake_token
            )
        except Exception as e:  # defensive — refresh already swallows
            logger.exception("QuixLake query failed")
            raise LeaderboardError(str(e)) from e
        best_laps_cache = live_telemetry.get_best_laps_cache()
        if best_laps_cache is None:
            raise LeaderboardError("QuixLake query failed; see backend logs")
    driver_name_lookup = _build_driver_name_lookup(mongo)

    try:
        active = live_telemetry.get_active_driver()
    except Exception:
        logger.exception("get_active_driver() raised; serving historical-only")
        active = None

    out: list[dict[str, object]] = []
    historical_keys = set(best_laps_cache.keys())
    for track, car, experiment, environment in sorted(historical_keys):
        out.extend(
            _build_group_rows(
                track,
                car,
                experiment,
                environment,
                best_laps_cache,
                driver_name_lookup,
                active,
            )
        )

    # Edge case: live driver is racing in a (track, car, experiment) that
    # has no historicals at all. Emit a 1-row solo group.
    if active:
        active_track = str(active.get("track") or "")
        active_car = str(active.get("car") or "")
        active_experiment = str(active.get("experiment") or "")
        if active_track and active_car and active_experiment:
            # The active driver might already be covered by one of the
            # historical groups (any environment under the same track/
            # car/experiment) — in that case the active row has already
            # been emitted by `_build_group_rows`. Skip the solo emit to
            # avoid a duplicate.
            already_emitted = any(
                k[0] == active_track
                and k[1] == active_car
                and k[2] == active_experiment
                for k in historical_keys
            )
            if not already_emitted:
                out.extend(_solo_active_group(active, driver_name_lookup))

    return out
