"""Real-mode `/leaderboard/live-positions` assembly.

This module powers the cloud path: query the Quix Lakehouse for
per-driver best laps, look up the live driver from `live_telemetry`,
and assemble the `LivePositionEntry` shape the frontend consumes.

Data source: ``LakehouseClient`` (``api/lakehouse_client.py``) — a
synchronous ``httpx`` shim against the Box Cloud Lakehouse Query API
(``POST {Quix__Lakehouse__Query__Url}/api/query``). Replaces the former
``QuixLakeClient`` / ``quixlake-sdk`` dependency.

Public entry point: `build_live_positions(mongo)`. Raises
`LeaderboardError` on configuration/upstream failures so the route
layer can map to a 500 with a useful `detail`.

See `docs/architecture-leaderboard-live-positions.md` for the full
design (file pending — see dev-planning/leaderboard-consolidated/spec.md).

# TODO: create docs/architecture-leaderboard-*.md
# (see dev-planning/leaderboard-consolidated/spec.md)

Why a separate module from `leaderboard.py`:

* `leaderboard.py` stays a thin router.  Everything that touches the
  lake, Mongo or the consumer state lives here.

Why no nested SQL / CTE: The Lakehouse Query API (DuckDB-backed)
silently returns 0 rows for queries that use ``WITH …``
(see `feedback_quixlake_no_cte`). The queries here are all
single-level ``GROUP BY`` with aggregations inside the SELECT.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import Any, Literal

import httpx
from pymongo.database import Database

from .. import live_telemetry
from ..lakehouse_client import LakehouseClient, LakehouseQueryError
from ..live_telemetry import GATE_COUNT, _HistoricalEntry, _resolve_display_name
from ..settings import get_settings

logger = logging.getLogger(__name__)


def _rank_group(rows: list[dict[str, object]]) -> None:
    """Sort `rows` in place by `rank_time_ms` (sticky cumulative time at
    the active driver's last-crossed gate), falling back to
    `current_lap_time_ms`. Assigns `rank` 1..N after sorting. Sorting on
    the snapshot prevents the active row from drifting in rank between
    gate crossings."""
    def _key(r: dict[str, object]) -> int:
        v = r.get("rank_time_ms")
        if v is None or v == 0:
            v = r.get("current_lap_time_ms")
        return int(v or 0)
    rows.sort(key=_key)
    for i, r in enumerate(rows):
        r["rank"] = i + 1


# Historicals per (track, car, experiment, environment) group. The UI
# collapses to 8 rows by default (rank 1 + 7 around the active driver)
# and expands to the full field on demand, so we ship up to 99 historicals
# per group (+ 1 active = max 100 rows) — enough headroom for any
# real-world driver field without paying for an unbounded payload.
_HISTORICAL_CAP_PER_GROUP = 99


# Lap-completeness threshold the gate-vectors reducer uses to distinguish
# a sparse-sample real lap (interpolated through) from a quit/crash/timeout
# (dropped entirely). See sc-71954-checkpoint-gates §5.2 step 4.
_PARTIAL_LAP_MAX_POS = 0.95


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


def _partition_filters(
    track: str, car: str, experiment: str, environment: str | None
) -> str:
    """WHERE-clause body matching the lake's Hive partitions.

    All four partition columns when `environment` is known (the rule for
    every known-group query); the 3-filter variant exists only for the
    dropdown best-laps route, where `environment` is unknown for arbitrary
    combos — a pre-existing, documented exception.
    """
    parts: list[str] = []
    if environment is not None:
        parts.append(f"environment = '{_format_sql_string(environment)}'")
    parts.extend(
        (
            f"track = '{_format_sql_string(track)}'",
            f"carModel = '{_format_sql_string(car)}'",
            f"experiment = '{_format_sql_string(experiment)}'",
        )
    )
    return " AND ".join(parts)


def _build_best_laps_sql(
    track: str, car: str, experiment: str, environment: str | None
) -> str:
    """Return the per-driver best-lap raw-scan SQL for one group.

    Uses AC's `iBestTime` (already a per-driver best lap in milliseconds;
    >0 means a completed lap exists). The lake streams raw
    `(driver, iBestTime)` rows; Python reduces to per-driver MIN. This is
    the fallback shape for `_query_best_laps_min` when server-side
    aggregation is disabled or fails.

    The WHERE filter matches the lake's Hive partitions (`environment`,
    `track`, `carModel`, `experiment`). `environment=None` emits the
    3-filter variant (dropdown route only — see `_partition_filters`).

    Table identifier is read from `settings.lake_table` (validated at
    settings load time against `[A-Za-z_][A-Za-z0-9_]*` so it is safe to
    inline directly into the SQL).
    """
    settings = get_settings()
    lake_table = settings.lake_table
    best_col = settings.col_best_time
    filters = _partition_filters(track, car, experiment, environment)
    return (
        f"SELECT driver, {best_col} "
        f"FROM {lake_table} "
        f"WHERE {filters} "
        f"AND {best_col} > 0"
    )


def _build_best_laps_min_sql(
    track: str, car: str, experiment: str, environment: str | None
) -> str:
    """Aggregated per-driver best-lap SQL (Query A, spec §5).

    `SELECT driver, MIN({best_col}) AS ms ... GROUP BY driver` —
    single-level GROUP BY, no CTE (QuixLake silently returns 0 rows for
    `WITH ...`; see `feedback_quixlake_no_cte`). Server-side aggregation
    is the telemetry-dashboard's proven cheap pattern
    (`telemetry-dashboard/main.py:257-262`); partition pruning via the
    full WHERE makes it cheaper still.
    """
    settings = get_settings()
    lake_table = settings.lake_table
    best_col = settings.col_best_time
    filters = _partition_filters(track, car, experiment, environment)
    return (
        f"SELECT driver, MIN({best_col}) AS ms "
        f"FROM {lake_table} "
        f"WHERE {filters} "
        f"AND {best_col} > 0 "
        f"GROUP BY driver"
    )


def _query_best_laps_min(
    quixlake_url: str,
    quix_lake_token: str,
    *,
    track: str,
    car: str,
    experiment: str,
    environment: str | None,
) -> dict[str, int]:
    """Query A: per-driver best lap for one group, `{raw_driver: best_ms}`.

    Returns **raw** lake driver strings — folding stays at the cache tail
    (`live_telemetry`) so the diff step can map folded keys back to the
    raw strings the narrowed Query B `driver IN (...)` clause needs.

    When `settings.lake_server_aggregation` is true (default), runs the
    aggregated `MIN(...) GROUP BY driver` SQL. On a lake error or HTTP
    failure it logs a warning and falls through to the raw-scan shape
    (`_build_best_laps_sql` + Python MIN) — the known-safe path when the
    configured table is a derived one where GROUP BY stalls
    (`feedback_quixlake_aggregation_slow`).

    Raises on failure of the raw-scan path (caller applies per-group
    stale-on-error).
    """
    client = LakehouseClient(base_url=quixlake_url, token=quix_lake_token)

    if get_settings().lake_server_aggregation:
        sql = _build_best_laps_min_sql(track, car, experiment, environment)
        logger.info("best-laps MIN SQL: %s", sql)
        try:
            df = client.query(sql)
            df = df.fillna("")
            rows: list[dict[str, Any]] = df.to_dict("records")
            per_driver: dict[str, int] = {}
            for row in rows:
                raw_driver = str(row.get("driver") or "").strip()
                if not raw_driver:
                    continue
                try:
                    best_ms = int(float(row.get("ms")))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    continue
                if best_ms <= 0:
                    continue
                prev = per_driver.get(raw_driver)
                if prev is None or best_ms < prev:
                    per_driver[raw_driver] = best_ms
            return per_driver
        except (LakehouseQueryError, httpx.HTTPError) as e:
            logger.warning(
                "server-side best-laps aggregation failed for "
                "track=%s car=%s exp=%s env=%s (%s); "
                "falling back to raw scan",
                track,
                car,
                experiment,
                environment,
                e,
            )

    # Raw-scan fallback: same rows `_query_best_laps` reads, but reduced
    # to RAW driver keys (no folding) so both paths return the same shape.
    sql = _build_best_laps_sql(track, car, experiment, environment)
    logger.info("best-laps scan SQL (fallback): %s", sql)
    df = client.query(sql)
    df = df.fillna("")
    scan_rows: list[dict[str, Any]] = df.to_dict("records")
    best_col = get_settings().col_best_time
    per_driver_raw: dict[str, int] = {}
    for row in scan_rows:
        raw_driver = str(row.get("driver") or "").strip()
        if not raw_driver:
            continue
        raw_best = row.get(best_col)
        if raw_best is None or raw_best == "":
            continue
        try:
            best_ms = int(float(raw_best))
        except (TypeError, ValueError):
            continue
        if best_ms <= 0:
            continue
        prev = per_driver_raw.get(raw_driver)
        if prev is None or best_ms < prev:
            per_driver_raw[raw_driver] = best_ms
    return per_driver_raw


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
    client = LakehouseClient(base_url=quixlake_url, token=quix_lake_token)
    df = client.query(sql)
    df = df.fillna("")
    rows: list[dict[str, Any]] = df.to_dict("records")

    # Per-driver MIN(iBestTime) folded in Python — the lake just streams
    # raw (driver, best_time) rows so the query stays a scan + filter
    # rather than a costly server-side aggregation.
    best_col = get_settings().col_best_time
    per_driver: dict[str, int] = {}
    for row in rows:
        raw_driver = str(row.get("driver") or "").strip()
        if not raw_driver:
            continue
        raw_best = row.get(best_col)
        if raw_best is None or raw_best == "":
            continue
        try:
            best_ms = int(float(raw_best))
        except (TypeError, ValueError):
            continue
        if best_ms <= 0:
            continue
        folded = _fold_driver_name(raw_driver)
        prev = per_driver.get(folded)
        if prev is None or best_ms < prev:
            per_driver[folded] = best_ms
    return per_driver


def _build_best_laps_with_lap_sql(
    track: str,
    car: str,
    experiment: str,
    environment: str,
    drivers: list[str] | None = None,
) -> str:
    """Return per-(driver, lap) lap-time SQL for one (track, car, exp, env)
    group.

    Emits raw `(driver, lap, iCurrentTime, normalizedCarPosition)` rows;
    Python reduces them per (driver, lap) afterwards (see
    `_reduce_lap_table`).

    Why this needs a separate query from `_query_best_laps_min`: that
    function aggregates to `MIN(iBestTime)` across the whole partition
    so it can't surface which lap the best was set on. The gate-samples
    query needs `(driver, lap)` triples, so we need the per-lap shape
    here.

    `drivers` (spec §6.3): when given, appends `AND driver IN (...)`
    (values escaped) so a targeted gate-vector rebuild only scans the
    changed drivers' rows instead of the whole partition.

    Table identifier is read from `settings.lake_table` (see
    `_build_best_laps_sql` for the validation contract).
    """
    settings = get_settings()
    lake_table = settings.lake_table
    cur_col = settings.col_current_time
    pos_col = settings.col_normalized_position
    drivers_clause = ""
    if drivers:
        quoted = ", ".join(f"'{_format_sql_string(d)}'" for d in drivers)
        drivers_clause = f" AND driver IN ({quoted})"
    return (
        f"SELECT driver, lap, {cur_col}, {pos_col} "
        f"FROM {lake_table} "
        f"WHERE environment = '{_format_sql_string(environment)}' "
        f"AND track = '{_format_sql_string(track)}' "
        f"AND carModel = '{_format_sql_string(car)}' "
        f"AND experiment = '{_format_sql_string(experiment)}' "
        f"AND lap > 0 AND {cur_col} > 0"
        f"{drivers_clause}"
    )


def _reduce_lap_table(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, int], tuple[int, float]]:
    """Reduce raw per-sample rows to `{(raw_driver, lap): (lap_ms, max_pos)}`.

    Lake returns raw (driver, lap, iCurrentTime, normalizedCarPosition)
    rows. iCurrentTime is AC's lap-relative clock (resets each lap), so
    MAX(iCurrentTime) per (driver, lap) is the real lap duration. The
    earlier MAX(timestamp_ms) - MIN(timestamp_ms) approach measured the
    lake ingestion window, picking partial laps as "fastest" whenever
    they happened to land in a small batch window.

    Also tracks max normalizedCarPosition per (driver, lap) — only laps
    whose samples cover ≥95% of the track are real laps; callers use
    `max_pos` against `_PARTIAL_LAP_MAX_POS` to filter the rest.
    """
    settings = get_settings()
    cur_col = settings.col_current_time
    pos_col = settings.col_normalized_position

    lap_max_time: dict[tuple[str, int], int] = {}
    lap_max_pos: dict[tuple[str, int], float] = {}
    for row in rows:
        raw_driver = str(row.get("driver") or "").strip()
        if not raw_driver:
            continue
        try:
            lap_num = int(row.get("lap") or 0)
            cur_ms = int(float(row.get(cur_col) or 0))
            pos = float(row.get(pos_col) or 0.0)
        except (TypeError, ValueError):
            continue
        if lap_num <= 0 or cur_ms <= 0:
            continue
        key = (raw_driver, lap_num)
        if cur_ms > lap_max_time.get(key, 0):
            lap_max_time[key] = cur_ms
        if pos > lap_max_pos.get(key, 0.0):
            lap_max_pos[key] = pos

    return {
        key: (lap_ms, lap_max_pos.get(key, 0.0))
        for key, lap_ms in lap_max_time.items()
    }


def _pick_fastest_complete(
    lap_table: dict[tuple[str, int], tuple[int, float]],
) -> dict[str, tuple[int, int]]:
    """`{raw_driver: (best_lap_ms, lap_number)}` — fastest complete lap.

    Rejects partial laps (coverage < `_PARTIAL_LAP_MAX_POS`) so the
    gate-samples reducer doesn't waste a roundtrip on them. This is the
    legacy discovery rule, kept as the fallback when no lap matches the
    Query A `iBestTime` within tolerance (spec §5).
    """
    per_driver: dict[str, tuple[int, int]] = {}
    for (raw_driver, lap_num), (lap_time_ms, max_pos) in lap_table.items():
        if lap_time_ms <= 0:
            continue
        if max_pos < _PARTIAL_LAP_MAX_POS:
            continue
        existing = per_driver.get(raw_driver)
        if existing is None or lap_time_ms < existing[0]:
            per_driver[raw_driver] = (lap_time_ms, lap_num)
    return per_driver


def _match_best_lap(
    per_driver_laps: dict[tuple[str, int], tuple[int, float]],
    driver: str,
    best_ms: int,
    tolerance_ms: int,
) -> tuple[int, int] | None:
    """Identify the lap Query A's `best_ms` was set on (spec §5 rule).

    Among `driver`'s complete laps (coverage ≥ `_PARTIAL_LAP_MAX_POS`),
    pick the one whose `MAX(iCurrentTime)` is closest to `best_ms`,
    accepted iff `|lap_ms - best_ms| <= tolerance_ms` (covers the
    up-to-one-sample-period undershoot of MAX(iCurrentTime) at low
    replay sample rates plus jitter). Returns `(lap_ms, lap_num)` or
    `None` when no complete lap is within tolerance — the caller then
    falls back to the fastest complete lap with a reconciliation warning.
    """
    best: tuple[int, int] | None = None
    best_diff: int | None = None
    for (raw_driver, lap_num), (lap_ms, max_pos) in per_driver_laps.items():
        if raw_driver != driver or lap_ms <= 0:
            continue
        if max_pos < _PARTIAL_LAP_MAX_POS:
            continue
        diff = abs(lap_ms - best_ms)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = (lap_ms, lap_num)
    if best is None or best_diff is None or best_diff > tolerance_ms:
        return None
    return best


def _query_lap_table(
    quixlake_url: str,
    quix_lake_token: str,
    *,
    track: str,
    car: str,
    experiment: str,
    environment: str,
    drivers: list[str] | None = None,
) -> dict[tuple[str, int], tuple[int, float]]:
    """Run the per-(driver, lap) scan and return the reduced lap table.

    `{(raw_driver, lap): (lap_ms, max_pos)}` — input for both
    `_match_best_lap` (Query A reconciliation) and
    `_pick_fastest_complete` (legacy rule / fallback).
    """
    sql = _build_best_laps_with_lap_sql(
        track, car, experiment, environment, drivers=drivers
    )
    logger.info("best-laps-with-lap SQL: %s", sql)
    client = LakehouseClient(base_url=quixlake_url, token=quix_lake_token)
    df = client.query(sql)
    df = df.fillna("")
    rows: list[dict[str, Any]] = df.to_dict("records")
    return _reduce_lap_table(rows)


def _query_best_laps_with_lap(
    quixlake_url: str,
    quix_lake_token: str,
    *,
    track: str,
    car: str,
    experiment: str,
    environment: str,
    drivers: list[str] | None = None,
) -> dict[str, tuple[int, int]]:
    """Return `{raw_driver: (best_lap_ms, lap_number)}` for one group.

    Thin wrapper: `_query_lap_table` + `_pick_fastest_complete`. Raw
    lake driver names are kept as-is — they match the same fold rule
    the gate-samples reducer keys on.
    """
    lap_table = _query_lap_table(
        quixlake_url,
        quix_lake_token,
        track=track,
        car=car,
        experiment=experiment,
        environment=environment,
        drivers=drivers,
    )
    return _pick_fastest_complete(lap_table)


# ---------------------------------------------------------------------------
# Gate-vectors pipeline (Left-table "Live Sector Comparison").
#
# Drives the per-gate colour cue on the active row. Refreshed on the same
# triggers as the best-laps cache (consumer startup warm-up, AC session
# message, DCM config event). See `live_telemetry.refresh_gate_vectors_cache`
# for the orchestration.
# ---------------------------------------------------------------------------


def _build_gate_samples_sql(
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
) -> str:
    """Per-lap position-sample SQL for every `(track, car, experiment,
    driver, lap)` in `best_per_group`.

    QuixLake doesn't support tuple-IN (see
    `feedback_quixlake_no_cte` and the original Step-2 spec); we fall
    back to a flat `OR` disjunction. Typical input size is ≤ 99 historicals
    per group × 1–2 active groups, which keeps the statement well within
    a single round-trip.
    """
    lake_table = get_settings().lake_table
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
    settings = get_settings()
    cur_col = settings.col_current_time
    pos_col = settings.col_normalized_position
    # Use the configured "current time" column (AC's lap clock — resets
    # each lap, ms-since-lap-start) rather than timestamp_ms (lake ingest
    # wall-clock). Column is `iCurrentTime` on the raw `ac_telemetry`
    # table and `currentTime` on derived leaderboard tables; the
    # LAKE_COL_CURRENT_TIME env var lets ops point at whichever exists.
    return (
        f"SELECT track, carModel, experiment, driver, session_id, lap, "
        f"{pos_col}, {cur_col} "
        f"FROM {lake_table} WHERE ({where}) "
        f"AND {pos_col} IS NOT NULL "
        f"AND {cur_col} > 0"
    )


def _query_gate_samples(
    quixlake_url: str,
    quix_lake_token: str,
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
) -> list[dict[str, Any]]:
    """Fetch position samples for the best laps in `best_per_group`."""
    sql = _build_gate_samples_sql(best_per_group)
    if "OR" not in sql and "AND lap=" not in sql:
        return []
    client = LakehouseClient(base_url=quixlake_url, token=quix_lake_token)
    logger.info(
        "Querying Lakehouse for gate-samples (%d best-lap rows)", len(best_per_group)
    )
    df = client.query(sql)
    df = df.fillna("")
    rows: list[dict[str, Any]] = df.to_dict("records")
    logger.info("Gate-samples query returned %d position rows", len(rows))
    if rows:
        sample = rows[0]
        logger.info(
            "Gate-samples sample row keys=%s values=%s",
            list(sample.keys()),
            {k: sample.get(k) for k in list(sample.keys())[:8]},
        )
    return rows


def _reduce_to_gate_vectors(
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
    sample_rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, _HistoricalEntry]]:
    """Bucket samples by `(track, car, exp, driver, lap)`, linear-interpolate
    each gate's cumulative time, and return the per-(track, car, exp)
    historicals dict keyed by **folded** driver name.

    The output is the canonical historicals cache for the Left-table
    colour cue. Folding only happens here at the tail of the pipeline so
    the SQL WHERE clause sees the raw lake string.
    """
    settings = get_settings()
    cur_col = settings.col_current_time
    pos_col = settings.col_normalized_position
    buckets: dict[tuple[str, str, str, str, str, int], list[tuple[float, int]]] = {}
    for row in sample_rows:
        try:
            ncp = float(row.get(pos_col) or 0.0)
            # `currentTime` (or `iCurrentTime` on the raw table) is AC's
            # lap clock — ms since lap start. That's exactly the
            # cumulative-time-at-position we cache for the gate vectors.
            ts_raw = row.get(cur_col)
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

    logger.info(
        "reduce_to_gate_vectors: %d sample rows → %d buckets; best_per_group=%s",
        len(sample_rows),
        len(buckets),
        {k: v for k, v in list(best_per_group.items())[:3]},
    )
    if buckets:
        first_key = next(iter(buckets))
        logger.info(
            "first bucket key=%s sample_count=%d",
            first_key,
            len(buckets[first_key]),
        )

    out: dict[tuple[str, str, str], dict[str, _HistoricalEntry]] = {}
    for (track, car, experiment, driver), (best_ms, lap_num) in best_per_group.items():
        candidate_keys = [
            k
            for k in buckets
            if k[0] == track
            and k[1] == car
            and k[2] == experiment
            and k[3] == driver
            and k[5] == lap_num
        ]
        if not candidate_keys:
            logger.info(
                "no bucket for historical driver=%s lap_num=%d (track=%s car=%s exp=%s)",
                driver,
                lap_num,
                track,
                car,
                experiment,
            )
            continue
        # Multiple session_ids can produce the same (driver, lap) key (e.g.
        # a driver ran lap 3 in multiple sessions). Sorting and picking
        # [0] used to grab whichever session_id sorted first — often a
        # partial-coverage run that bounced as `max_pos < 0.95`. Pick the
        # session with the widest normPos coverage instead.
        best_key = max(
            candidate_keys, key=lambda k: max(s[0] for s in buckets[k])
        )
        # Sort by iCurrentTime ascending; iCurrentTime IS the lap-relative
        # ms-since-start, so no lap_start subtraction is needed downstream.
        samples = sorted(buckets[best_key], key=lambda x: x[1])
        if not samples:
            continue
        max_pos = max(s[0] for s in samples)
        if max_pos < _PARTIAL_LAP_MAX_POS:
            logger.info(
                "drop partial lap driver=%s lap_num=%d max_pos=%.4f (< %.2f)",
                driver,
                lap_num,
                max_pos,
                _PARTIAL_LAP_MAX_POS,
            )
            continue

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
            gate_vector[i] = max(0, int(interp_ts))

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


# Re-exports from `gate_math` so the legacy import path
# `from .leaderboard_real import compute_last_gate_state` (and friends)
# keeps working. Both the snapshot-rebuild path here and the per-tick
# path in `live_telemetry._record_message` MUST go through the same
# helper — see `api/gate_math.py` for the formula.
from ..gate_math import (  # noqa: E402  re-export
    compute_last_gate_state as _compute_last_gate_state_shared,
)
from ..gate_math import (  # noqa: E402  re-export
    compute_per_historical_deltas as _compute_per_historical_deltas_shared,
)


def compute_last_gate_state(
    active_gate_times: list[int | None],
    historicals: dict[str, _HistoricalEntry] | None,
) -> tuple[
    int | None,
    Literal["ahead", "behind", "neutral"] | None,
    int | None,
]:
    """Re-export wrapper that supplies the GATE_COUNT to `gate_math`.

    Kept for backward compatibility with any caller importing from the
    old location. New code should call `gate_math.compute_last_gate_state`
    directly.
    """
    return _compute_last_gate_state_shared(active_gate_times, historicals, GATE_COUNT)


def _compute_per_historical_deltas(
    active_gate_times: list[int | None],
    historicals: dict[str, _HistoricalEntry] | None,
) -> dict[str, int]:
    """Local-arity wrapper around `gate_math.compute_per_historical_deltas`."""
    return _compute_per_historical_deltas_shared(
        active_gate_times, historicals, GATE_COUNT
    )


# ---------------------------------------------------------------------------
# Row factories
# ---------------------------------------------------------------------------


def _historical_row(
    track: str,
    car: str,
    experiment: str,
    display_driver: str,
    best_lap_ms: int,
    *,
    best_lap_number: int | None = None,
    last_gate_index: int | None = None,
    delta_at_last_gate_ms: int | None = None,
    gate_time_at_active_ms: int = 0,
    rank_time_ms: int = 0,
) -> dict[str, object]:
    """Build a `LivePositionEntry`-shaped dict for a historical driver.

    `last_gate_index` is echoed onto historical rows so the frontend
    knows which gate index the per-row `delta_at_last_gate_ms` was
    computed against (this is the active driver's `last_gate_index`,
    not a per-historical one — historicals don't move during a poll).
    `last_gate_state` and `last_gate_delta_ms` stay `None` on
    historicals; those are active-row-only fields.
    """
    return {
        "track": track,
        "car": car,
        "experiment": experiment,
        "driver": display_driver,
        "best_lap_ms": best_lap_ms,
        "best_lap_number": best_lap_number,
        "is_active": False,
        "current_lap": None,
        # `current_lap_time_ms` doubles as the "At Position" column. For
        # historicals we project their cumulative gate-time at whatever
        # gate the active driver most recently crossed so the column
        # "ticks" as the active progresses (instead of frozen at 0).
        "current_lap_time_ms": gate_time_at_active_ms,
        # Rank sort key — see `live_positions_sim.rank_group`. Uses the
        # cumulative time at the LAST crossed gate (not the display
        # column's next-gate value) so rank only snaps at crossings.
        "rank_time_ms": rank_time_ms or gate_time_at_active_ms,
        "rank": 0,
        "last_gate_index": last_gate_index,
        "last_gate_state": None,
        "last_gate_delta_ms": None,
        "delta_at_last_gate_ms": delta_at_last_gate_ms,
    }


def _active_row(
    track: str,
    car: str,
    experiment: str,
    display_driver: str,
    best_lap_ms: int | None,
    current_lap: int,
    current_lap_time_ms: int,
    *,
    best_lap_number: int | None = None,
    last_gate_index: int | None = None,
    last_gate_state: str | None = None,
    last_gate_delta_ms: int | None = None,
    rank_time_ms: int = 0,
) -> dict[str, object]:
    """Build a `LivePositionEntry`-shaped dict for the active driver.

    Gate-state fields default to `None` (callers pass them in once the
    Left-table colour cue has data). `delta_at_last_gate_ms` is always
    `None` on the active row — that column is per-historical only.
    """
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
        # Sort key: active's cumulative iCurrentTime at the LAST crossed
        # gate. Sticky between crossings — matches the same gate the
        # historicals are ranked by. Falls back to live current_lap_time_ms
        # if no gate has been crossed yet (lap start).
        "rank_time_ms": rank_time_ms or max(0, int(current_lap_time_ms)),
        "rank": 0,
        "last_gate_index": last_gate_index,
        "last_gate_state": last_gate_state,
        "last_gate_delta_ms": last_gate_delta_ms,
        "delta_at_last_gate_ms": None,
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


def historical_next_gate_value(
    entry: _HistoricalEntry | None,
    last_gate_index: int | None,
    gate_count: int = GATE_COUNT,
) -> int:
    """Return the historical's `gate_vector[i*+1]` (clamped to last gate).

    Single helper shared between this module (HTTP snapshot path) and
    `live_telemetry._record_message` (WS active mutation path) so both
    paths emit the same "next gate" value for the live-mode "At
    Position" display (spec §7.7 reconciliation).

    Pre-gate-1 fallback (spec §4.1): when `last_gate_index is None`
    (lap just started, no gate crossed yet) we return `gate_vector[0]`
    — gate 1's cumulative time — so historicals' ordering still
    reflects the lake's real per-driver shape rather than collapsing
    to 0 and tying everyone for rank 1.
    """
    if entry is None:
        return 0
    n = len(entry.gate_vector)
    if n == 0:
        return 0
    if last_gate_index is None:
        # Pre-gate-1: surface gate 1's cumulative time. The active driver's
        # `rank_time_ms` is his live `current_lap_time_ms` in this case
        # (spec §4.1), so historicals at `gate_vector[0]` give a stable,
        # sensible-magnitude reference instead of 0.
        return int(entry.gate_vector[0])
    next_idx = min(last_gate_index + 1, gate_count - 1, n - 1)
    if 0 <= next_idx < n:
        return int(entry.gate_vector[next_idx])
    return 0


def historical_at_crossing_value(
    entry: _HistoricalEntry | None,
    last_gate_index: int | None,
    gate_count: int = GATE_COUNT,
) -> int:
    """Return the historical's `gate_vector[i*]` (clamped to last gate).

    Used by the rank metric (sticky at the last crossed gate). Pair of
    `historical_next_gate_value` for symmetry / shared call sites.

    Pre-gate-1 fallback (spec §4.1): when `last_gate_index is None` we
    return `gate_vector[0]` so rank ordering pre-first-gate matches the
    lake's per-driver shape — see `historical_next_gate_value` for the
    same rationale.
    """
    if entry is None:
        return 0
    n = len(entry.gate_vector)
    if n == 0:
        return 0
    if last_gate_index is None:
        return int(entry.gate_vector[0])
    last_idx = min(last_gate_index, gate_count - 1, n - 1)
    if 0 <= last_idx < n:
        return int(entry.gate_vector[last_idx])
    return 0


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
    gate_vectors_cache: dict[tuple[str, str, str], dict[str, _HistoricalEntry]] | None,
) -> list[dict[str, object]]:
    """Assemble the rows for one (track, car, experiment, environment)
    group: historicals from the lake cache + the active driver (if it
    matches this group). Returns a ranked list of `LivePositionEntry`-
    shaped dicts.

    Populates `last_gate_*` on the active row from the gate-vectors
    cache (Left-table colour cue) and `delta_at_last_gate_ms` per
    historical inline (spec §7.2). Both share the same `last_gate_index`
    so the frontend renders consistent labels.
    """
    group_historicals = best_laps_cache.get((track, car, experiment, environment), {})
    group_gate_vectors = (
        gate_vectors_cache.get((track, car, experiment), {})
        if gate_vectors_cache
        else {}
    )

    # Active-row detection. When the active driver collides with a
    # historical by folded key (spec §3.8), the historical MUST be
    # suppressed — we render a single merged row keyed off the active.
    active_in_group = bool(
        active
        and active.get("track") == track
        and active.get("car") == car
        and (active.get("experiment") or "") == experiment
    )
    active_folded: str | None = None
    if active_in_group and active is not None:
        active_folded = _fold_driver_name(str(active.get("driver") or ""))

    candidates: list[tuple[int, str]] = [
        (best_ms, folded_driver)
        for folded_driver, best_ms in group_historicals.items()
        # Spec §3.8: drop the historical that collides with the active
        # driver's folded key — that row is emitted as the merged active
        # row further down. Lake `"ludvik"` + Mongo `"Ludvík"` → one row.
        if folded_driver != active_folded
    ]
    candidates.sort(key=lambda x: x[0])
    candidates = candidates[:_HISTORICAL_CAP_PER_GROUP]

    # Pre-compute the per-historical inline deltas + active-row sticky
    # triple. Both are gated on the active row matching this group; if
    # there is no active row, both are empty/None and historicals get
    # `delta_at_last_gate_ms = None`.
    per_historical_deltas: dict[str, int] = {}
    sticky_last_gate_index: int | None = None
    sticky_last_gate_state: str | None = None
    sticky_last_gate_delta_ms: int | None = None
    if active_in_group and active is not None:
        active_gate_times = list(active.get("gate_times_ms") or [])
        if active_gate_times:
            (
                sticky_last_gate_index,
                sticky_last_gate_state,
                sticky_last_gate_delta_ms,
            ) = compute_last_gate_state(active_gate_times, group_gate_vectors or None)
            per_historical_deltas = _compute_per_historical_deltas(
                active_gate_times, group_gate_vectors or None
            )
        # Fall back to whatever the consumer thread persisted on the
        # state entry — keeps the sticky fields populated between gate
        # crossings when the snapshot is rebuilt mid-sector.
        if sticky_last_gate_index is None and active.get("last_gate_index") is not None:
            sticky_last_gate_index = active.get("last_gate_index")
            sticky_last_gate_state = active.get("last_gate_state")
            sticky_last_gate_delta_ms = active.get("last_gate_delta_ms")

    rows: list[dict[str, object]] = []
    for best_ms, folded_driver in candidates:
        # Centralised display-case resolution (spec §7.2) — same helper
        # the WS broadcaster uses so HTTP snapshot driver names match the
        # `{"type": "active"}` envelope exactly.
        display_driver = _resolve_display_name(folded_driver, driver_name_lookup)
        entry = group_gate_vectors.get(folded_driver)
        best_lap_number = entry.best_lap_number if entry else None
        # The per-row delta uses the active driver's last_gate_index; if
        # we have no active row OR the active driver hasn't crossed gate
        # 1 yet, all historicals carry `None`.
        row_delta = per_historical_deltas.get(folded_driver)
        # Historical's cumulative lap-time at the NEXT gate ahead of the
        # active driver (display) and at the LAST crossed gate (rank).
        # Display = gate (N+1) so the column shows the upcoming target.
        # Rank   = gate N so the order only snaps at gate crossings.
        # Both pulled from the same helpers the WS active mutation uses
        # (spec §7.7 reconciliation): one source of truth per value.
        gate_time_next = historical_next_gate_value(entry, sticky_last_gate_index)
        gate_time_last = historical_at_crossing_value(entry, sticky_last_gate_index)
        rows.append(
            _historical_row(
                track=track,
                car=car,
                experiment=experiment,
                display_driver=display_driver,
                best_lap_ms=best_ms,
                best_lap_number=best_lap_number,
                last_gate_index=sticky_last_gate_index,
                delta_at_last_gate_ms=row_delta,
                gate_time_at_active_ms=gate_time_next,
                rank_time_ms=gate_time_last,
            )
        )

    # Inject the active row only when its (track, car, experiment) matches
    # this group. `environment` is not on the live snapshot — every active
    # driver lives in exactly one DCM experiment config (and therefore one
    # environment), so we can't end up rendering the same active driver in
    # two groups for different environments.
    if active_in_group and active is not None and active_folded is not None:
        # Use the centralised resolver — same fallback as WS broadcaster,
        # so HTTP snapshot driver === WS `{"type":"active"}` driver.
        # Mismatch freezes the live timer (frontend match is exact-equal).
        display_driver = _resolve_display_name(active_folded, driver_name_lookup)
        lake_best = group_historicals.get(active_folded)
        i_last_time = active.get("best_lap_ms_session")
        try:
            i_last_int: int | None = int(i_last_time) if i_last_time else None
        except (TypeError, ValueError):
            i_last_int = None
        active_best = _best_for_active(lake_best, i_last_int)
        # Spec §3.8: the merged row inherits the lake's best_lap_number for
        # the folded driver — Mongo display case, lake-derived best.
        active_entry = group_gate_vectors.get(active_folded)
        active_best_lap_number = (
            active_entry.best_lap_number if active_entry is not None else None
        )

        try:
            current_lap_time_ms = int(active.get("current_lap_time_ms") or 0)
        except (TypeError, ValueError):
            current_lap_time_ms = 0
        try:
            current_lap = int(active.get("current_lap") or 1)
        except (TypeError, ValueError):
            current_lap = 1

        # Rank by active's cumulative iCurrentTime AT the last crossed
        # gate (sticky between crossings) — matches the gate index used
        # for historicals' rank_time_ms. Falls back to live time if no
        # gate crossed yet (i.e. i* is None — pre-gate-1).
        active_gate_times = list(active.get("gate_times_ms") or [])
        if (
            sticky_last_gate_index is not None
            and 0 <= sticky_last_gate_index < len(active_gate_times)
            and active_gate_times[sticky_last_gate_index] is not None
        ):
            active_rank_time = int(active_gate_times[sticky_last_gate_index])
        else:
            # Spec §3.7 fallback: only when i* is None (pre-gate-1).
            active_rank_time = current_lap_time_ms

        rows.append(
            _active_row(
                track=track,
                car=car,
                experiment=experiment,
                display_driver=display_driver,
                best_lap_ms=active_best,
                current_lap=current_lap,
                current_lap_time_ms=current_lap_time_ms,
                best_lap_number=active_best_lap_number,
                last_gate_index=sticky_last_gate_index,
                last_gate_state=sticky_last_gate_state,
                last_gate_delta_ms=sticky_last_gate_delta_ms,
                rank_time_ms=active_rank_time,
            )
        )

    _rank_group(rows)
    return rows


def _solo_active_group(
    active: dict[str, Any],
    driver_name_lookup: dict[str, str],
) -> list[dict[str, object]]:
    """Emit a 1-row group for a live driver whose (track, car, exp, env)
    has no historical entries in the lake yet.

    Cold-cache historicals -> `last_gate_state = "neutral"` (no colour
    paint), but `last_gate_index` still tracks the active driver's
    progress so the frontend can render the row index in the gate
    breakdown column.
    """
    raw_driver = str(active.get("driver") or "")
    folded_key = _fold_driver_name(raw_driver)
    # Centralised display-case resolver — same fallback chain as WS
    # broadcaster output so HTTP snapshot and `{"type": "active"}`
    # mutations carry identical `driver` strings.
    display_driver = (
        _resolve_display_name(folded_key, driver_name_lookup) if folded_key
        else raw_driver
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

    # Empty historicals → cold cache; gate_math returns
    # `(i*, "neutral", None)` once gate 1 is crossed.
    active_gate_times = list(active.get("gate_times_ms") or [])
    last_gate_index, last_gate_state, last_gate_delta_ms = compute_last_gate_state(
        active_gate_times, None
    )
    if last_gate_index is None and active.get("last_gate_index") is not None:
        last_gate_index = active.get("last_gate_index")
        last_gate_state = active.get("last_gate_state")
        last_gate_delta_ms = active.get("last_gate_delta_ms")

    row = _active_row(
        track=str(active.get("track") or ""),
        car=str(active.get("car") or ""),
        experiment=str(active.get("experiment") or ""),
        display_driver=display_driver,
        best_lap_ms=i_last_int,
        current_lap=current_lap,
        current_lap_time_ms=current_lap_time_ms,
        last_gate_index=last_gate_index,
        last_gate_state=last_gate_state,
        last_gate_delta_ms=last_gate_delta_ms,
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
    if not settings.lakehouse_query_url or not settings.lakehouse_query_token:
        raise LeaderboardError("Lakehouse credentials missing")

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
                settings.lakehouse_query_url, settings.lakehouse_query_token
            )
        except Exception as e:  # defensive — refresh already swallows
            logger.exception("Lakehouse query failed")
            raise LeaderboardError(str(e)) from e
        best_laps_cache = live_telemetry.get_best_laps_cache()
        if best_laps_cache is None:
            raise LeaderboardError("Lakehouse query failed; see backend logs")
    driver_name_lookup = _build_driver_name_lookup(mongo)

    try:
        active = live_telemetry.get_active_driver()
    except Exception:
        logger.exception("get_active_driver() raised; serving historical-only")
        active = None

    # The gate-vectors cache may legitimately be `None` (cold start) or
    # empty for a (track, car, experiment) — `_build_group_rows` and the
    # gate-state helpers guard against that, so we pass it through and
    # let the row-level code degrade to "no colour cue, no historical
    # deltas".
    gate_vectors_cache = live_telemetry.get_gate_vectors_cache()

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
                gate_vectors_cache,
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
