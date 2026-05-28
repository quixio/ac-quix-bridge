"""Real-mode `/leaderboard/live-positions` assembly.

LOCAL_DEV_MODE stays in `live_positions_sim`. This module powers the
cloud path: query QuixLake for historical best laps + their per-gate
cumulative-time vectors, look up the live driver from `live_telemetry`,
and assemble the same `LivePositionEntry` shape the frontend already
consumes.

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
that use `WITH …` (see `feedback_quixlake_no_cte`). The per-driver
best-lap reduction happens in Python (`_reduce_to_per_driver_best`),
and the per-gate cumulative-time vectors are built in Python from a
single follow-up sample query (`_query_gate_samples` +
`_reduce_to_gate_vectors`).
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


# Max distance (in normalized position units) between a gate's target
# position and the nearest sample we'll accept. Half a gate spacing
# (1/20 = 0.05 → half = 0.025). If a historical's best lap is missing
# samples around a gate, we drop that historical from the cache rather
# than ship a fabricated time (spec §5.2 reducer step 4).
_GATE_PICK_TOLERANCE = 1.0 / GATE_COUNT / 2.0


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
# Gate-vectors: per-best-lap sample query + Python reduction
# ---------------------------------------------------------------------------


def _format_sql_string(value: str) -> str:
    """Single-quote-escape a string for inline use in a SQL literal.

    QuixLake's HTTP `/query` endpoint takes a raw SQL string — no
    parameterised queries are exposed via `QuixLakeClient.query()`. We
    inline the IN-clause values and escape single quotes by doubling
    them (ANSI SQL convention; DuckDB, Postgres, and ClickHouse all
    accept this form).
    """
    return value.replace("'", "''")


def _build_gate_samples_sql(
    best_per_group: dict[tuple[str, str, str, str], tuple[int, int]],
) -> str:
    """Return the WHERE-clause-laden SQL for the gate-samples query.

    Uses a flat OR-of-AND disjunction rather than tuple-`IN`. Tuple-`IN`
    is non-portable (DuckDB supports it; ClickHouse needs a different
    syntax; QuixLake's exact backend isn't documented), and our cap of
    99 historicals × ~1 active group keeps the disjunction comfortably
    under any statement-size budget. We commit to disjunction-form here
    for determinism — see commit message body for the rationale.
    """
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
    # `session_id` is included in the SELECT so the reducer can group
    # multiple (driver, lap) collisions across sessions correctly. The
    # WHERE clause already picks one specific lap per driver via
    # `_reduce_to_per_driver_best`, but a driver's best lap number could
    # repeat across sessions; the reducer keys by session_id to keep
    # them separate.
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
    """Fetch position samples for the best laps in `best_per_group`.

    Single query against QuixLake. The disjunction WHERE clause keeps
    the result set bounded to one specific lap per (driver) so the
    Python reducer only has to pick the gate-nearest sample per gate.
    """
    sql = _build_gate_samples_sql(best_per_group)
    if "OR" not in sql and "AND lap=" not in sql:
        # Empty disjunction (no eligible historicals). Skip the round
        # trip and return immediately — the reducer handles an empty
        # input gracefully.
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
    """Bucket samples by (track, car, exp, driver, lap), then pick the
    nearest sample per gate to build each historical's 20-element
    cumulative-time vector.

    Drops a historical entirely if any of its 20 gates has no sample
    within `_GATE_PICK_TOLERANCE` of the target position (spec §5.2
    reducer step 4). Logs the drop at INFO.

    Output is keyed by (track, car, experiment) at the outer level and
    by **folded driver name** at the inner level — matches the lookup
    pattern in `_build_group_rows`.
    """
    # Bucket samples by the same key the best-lap dict uses + session_id
    # (so multiple-session collisions on the same lap number don't
    # contaminate each other).
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

    # Pick one bucket per (track, car, exp, driver) — the one whose
    # (session_id, lap) matches `best_per_group`'s pick. Multiple
    # sessions may have the same driver hitting the same lap number;
    # `_reduce_to_per_driver_best` already chose one, so pick the
    # corresponding bucket here.
    out: dict[tuple[str, str, str], dict[str, _HistoricalEntry]] = {}
    for (track, car, experiment, driver), (best_ms, lap_num) in best_per_group.items():
        # Find the bucket whose key matches this best-lap pick. Walk all
        # session_ids — the per-driver-best reduction doesn't expose the
        # winning session_id, so we accept whichever session has a bucket
        # matching `lap_num` and use *its* samples. In the typical case
        # (one driver = one fast lap on one session) there's exactly one
        # candidate; on collisions we pick the smallest session_id
        # alphabetically for determinism.
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
            logger.info(
                "gate-vectors: no samples for best lap "
                "track=%s car=%s exp=%s driver=%s lap=%d — dropping",
                track,
                car,
                experiment,
                driver,
                lap_num,
            )
            continue
        samples = sorted(buckets[candidate_keys[0]], key=lambda x: x[1])
        if not samples:
            continue

        lap_start_ms = samples[0][1]
        gate_vector: list[int] = [0] * GATE_COUNT
        ok = True
        for i in range(GATE_COUNT):
            target = (i + 1) / GATE_COUNT
            # Tie-break: prefer earliest timestamp when two samples are
            # equidistant from the target. `samples` is already sorted
            # by timestamp, so a stable sort by distance preserves that
            # tie-break.
            nearest = min(samples, key=lambda s, t=target: abs(s[0] - t))
            if abs(nearest[0] - target) > _GATE_PICK_TOLERANCE:
                logger.info(
                    "gate-vectors: gate %d (pos %.2f) missing within tol for "
                    "track=%s car=%s exp=%s driver=%s — dropping driver",
                    i,
                    target,
                    track,
                    car,
                    experiment,
                    driver,
                )
                ok = False
                break
            gate_vector[i] = max(0, nearest[1] - lap_start_ms)

        if not ok:
            continue
        # Force monotonic non-decreasing as a safety net (off-track
        # back-tracking on the source lap could yield a non-monotonic
        # pick).
        for i in range(1, GATE_COUNT):
            if gate_vector[i] < gate_vector[i - 1]:
                gate_vector[i] = gate_vector[i - 1]

        # `_HistoricalEntry.best_lap_ms` == gate_vector[-1] by
        # definition. The lake's `best_ms` (from `_BEST_LAPS_SQL`'s
        # `MAX(ts)-MIN(ts)`) and our `gate_vector[19]` should match
        # within rounding; we trust the gate-vector version because
        # it's literally what the colour-state comparison reads.
        entry = _HistoricalEntry(
            best_lap_ms=int(gate_vector[GATE_COUNT - 1]) or int(best_ms),
            best_lap_number=int(lap_num),
            gate_vector=gate_vector,
        )
        group_key = (track, car, experiment)
        out.setdefault(group_key, {})[_fold_driver_name(driver)] = entry
    return out


# ---------------------------------------------------------------------------
# Ghost interpolation from real gate vectors
# ---------------------------------------------------------------------------


def ghost_ms_at_position(gate_vector: list[int], p: float) -> int:
    """Linear-interpolate a historical's cumulative-ms at position `p`.

    Implicit (0% gate, 0 ms) → (5% gate, gate_vector[0]) → … →
    (100% gate, gate_vector[19]). Used for each historical's
    `current_lap_time_ms` column on every poll (spec §5.5 replaces the
    `EQUAL_SPLITS` ghost).

    A `p` outside `[0, 1]` is clamped; a malformed vector (wrong length)
    falls back to a proportional estimate so the leaderboard never
    crashes on a partial cache.
    """
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


# ---------------------------------------------------------------------------
# Gate-state computation (spec §5.4)
# ---------------------------------------------------------------------------


def _latest_crossed_gate(gate_times_ms: list[int | None]) -> int | None:
    """Highest index `i` with `gate_times_ms[i] is not None`, or `None`."""
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
    """Return `(last_gate_index, last_gate_state, last_gate_delta_ms)` for
    the active driver.

    Logic per spec §5.4:
      * `i*` = latest crossed gate index on the active driver's lap.
      * If `i*` is `None` (no crossings yet this lap) → all three None.
      * If no historicals (cold cache / empty group) → `(i*, "neutral",
        None)`.
      * Otherwise compare `active.gate_times_ms[i*]` against every
        cached `historical.gate_vector[i*]`:
          - strictly faster than every historical → "ahead"
          - strictly slower than every historical → "behind"
          - mixed (or ties) → "neutral" (the `all(<)`/`all(>)` check
            naturally falls into neutral on ties; no explicit branch
            needed — see spec §8.8)

    `last_gate_delta_ms` = active - min(historicals' gate_vector[i*])
    where positive means the active is behind the leader at the gate.
    `None` when there are no historicals to compare against.
    """
    i_star = _latest_crossed_gate(active_gate_times)
    if i_star is None:
        return None, None, None
    active_t = active_gate_times[i_star]
    if active_t is None:
        return None, None, None
    if not historicals:
        # Cold cache or empty group: gate index is known, but no judgement
        # was made — emit "neutral" so the row renders with the default
        # text colour while still advertising the gate index for any
        # client that wants to display it.
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
        # Mixed bag, or active is equal to at least one historical at
        # this gate. Ties aren't a clear win or loss — both `all(<)` and
        # `all(>)` are false, so we land here. Intentional (§8.8).
        state = "neutral"
    delta_ms = int(active_t - min(hist_ts))
    return i_star, state, delta_ms


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
    last_gate_index: int | None = None,
    last_gate_state: Literal["ahead", "behind", "neutral"] | None = None,
    last_gate_delta_ms: int | None = None,
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
        "last_gate_index": last_gate_index,
        "last_gate_state": last_gate_state,
        "last_gate_delta_ms": last_gate_delta_ms,
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
    gate_vectors_cache: dict[tuple[str, str, str], dict[str, _HistoricalEntry]],
) -> set[tuple[str, str, str]]:
    return set(gate_vectors_cache.keys())


def _historicals_for_group(
    historicals: dict[str, _HistoricalEntry],
    driver_name_lookup: dict[str, str],
    track: str,
    car: str,
    experiment: str,
    norm_pos: float,
) -> list[dict[str, object]]:
    """Cap of fastest historicals for one (track, car, experiment).

    Each historical's `current_lap_time_ms` is the real-gate-interpolated
    ghost estimate at the live driver's `normalizedCarPosition`. Spec
    §5.5 replaced the old `EQUAL_SPLITS` ghost with a piecewise-linear
    interpolation over the cached 20-element gate vector.
    """
    candidates: list[tuple[int, int, str, list[int]]] = []
    for folded_driver, entry in historicals.items():
        candidates.append(
            (entry.best_lap_ms, entry.best_lap_number, folded_driver, entry.gate_vector)
        )
    candidates.sort(key=lambda x: x[0])
    candidates = candidates[:_HISTORICAL_CAP_PER_GROUP]

    rows: list[dict[str, object]] = []
    for best_ms, lap_num, folded_driver, gate_vector in candidates:
        display_driver = driver_name_lookup.get(folded_driver, folded_driver)
        ghost_ms = ghost_ms_at_position(gate_vector, norm_pos)
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
    gate_vectors_cache: dict[tuple[str, str, str], dict[str, _HistoricalEntry]],
    driver_name_lookup: dict[str, str],
    active: dict[str, Any] | None,
) -> list[dict[str, object]]:
    """Assemble the rows for one (track, car, experiment) group.

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

    group_historicals = gate_vectors_cache.get((track, car, experiment), {})
    rows = _historicals_for_group(
        group_historicals, driver_name_lookup, track, car, experiment, norm_pos
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
        # Look up the active driver's own historical entry by folded
        # name. Lake partitions are lowercased + diacritic-preserving,
        # but the cache is indexed by NFKD+ASCII fold so a Mongo
        # "Ludvík" collides with a lake "ludvik".
        folded = _fold_driver_name(raw_driver)
        own_entry = group_historicals.get(folded)
        lake_best = own_entry.best_lap_ms if own_entry else None
        lake_lap = own_entry.best_lap_number if own_entry else None

        i_last_time = active.get("best_lap_ms_session")
        try:
            i_last_int: int | None = int(i_last_time) if i_last_time else None
        except (TypeError, ValueError):
            i_last_int = None
        active_best = _best_for_active(lake_best, i_last_int)
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

        # Compute the current gate-state from the active driver's gate
        # crossings vs. cached historicals. Recompute only when a new
        # crossing has happened since the last poll; otherwise re-emit
        # the sticky value persisted on `live_telemetry._state` (spec
        # §5.4 "server-side stickiness").
        active_gate_times = list(active.get("gate_times_ms") or [None] * GATE_COUNT)
        new_i_star = _latest_crossed_gate(active_gate_times)
        prev_i_star = active.get("last_gate_index")
        if new_i_star is not None and new_i_star != prev_i_star:
            last_index, last_state, last_delta = compute_last_gate_state(
                active_gate_times, group_historicals or None
            )
            live_telemetry.set_last_gate_state(
                str(active.get("track") or ""),
                str(active.get("car") or ""),
                raw_driver,
                last_index,
                last_state,
                last_delta,
            )
        else:
            # No new crossing. Re-emit the stored sticky values; on a
            # fresh lap (prev cleared to None) this yields all-None,
            # which the frontend renders as the default colour.
            last_index = int(prev_i_star) if isinstance(prev_i_star, int) else None
            last_state_raw = active.get("last_gate_state")
            last_state = (
                last_state_raw
                if last_state_raw in ("ahead", "behind", "neutral")
                else None
            )
            last_delta_raw = active.get("last_gate_delta_ms")
            last_delta = (
                int(last_delta_raw) if isinstance(last_delta_raw, int) else None
            )

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
                last_gate_index=last_index,
                last_gate_state=last_state,
                last_gate_delta_ms=last_delta,
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

    Cold-cache colour rule (spec §7 scenario 7): no historicals to
    compare → `last_gate_state = "neutral"` regardless of gate index, so
    the row renders with the default text colour.
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

    active_gate_times = list(active.get("gate_times_ms") or [None] * GATE_COUNT)
    last_index, last_state, last_delta = compute_last_gate_state(
        active_gate_times, None
    )

    row = _active_row(
        track=str(active.get("track") or ""),
        car=str(active.get("car") or ""),
        experiment=str(active.get("experiment") or ""),
        display_driver=display_driver,
        best_lap_ms=i_last_int,
        best_lap_number=None,
        current_lap=current_lap,
        current_lap_time_ms=current_lap_time_ms,
        last_gate_index=last_index,
        last_gate_state=last_state,
        last_gate_delta_ms=last_delta,
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

    # Read from the in-process gate-vectors cache instead of hitting the
    # lake on every poll. The cache is refreshed by `live_telemetry`'s
    # session handler (once per AC session start) and at consumer warm-up;
    # the per-request path here is now lake-free in the common case.
    gate_vectors_cache = live_telemetry.get_gate_vectors_cache()
    if gate_vectors_cache is None:
        # Cold start: no refresh has run yet (consumer thread might be
        # disabled or hasn't reached its warm-up). Do one synchronous
        # refresh so the first poll after backend boot still serves data.
        # `refresh_gate_vectors_cache` swallows its own exceptions, so we
        # need to re-check afterwards and surface upstream failures as
        # `LeaderboardError` only when the cache is still empty.
        try:
            live_telemetry.refresh_gate_vectors_cache(
                settings.quixlake_url, settings.quix_lake_token
            )
        except Exception as e:  # defensive — refresh should already swallow
            logger.exception("QuixLake query failed")
            raise LeaderboardError(str(e)) from e
        gate_vectors_cache = live_telemetry.get_gate_vectors_cache()
        if gate_vectors_cache is None:
            # Refresh failed (logged inside refresh_gate_vectors_cache) and
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
    historical_keys = _group_keys(gate_vectors_cache)
    for track, car, experiment in sorted(historical_keys):
        out.extend(
            _build_group_rows(
                track,
                car,
                experiment,
                gate_vectors_cache,
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
