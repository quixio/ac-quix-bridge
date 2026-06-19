"""Cold-start lakehouse seed: raw scan -> per-lap reduction -> gate vectors.

The authoritative source of best laps is the live ``ac-telemetry-raw`` topic; the
lakehouse is queried **only to seed a genuinely empty State** (fresh consumer
group / wiped volume). Two-stage, reusing the service's proven byox-safe scan:

1. One full raw scan of ``(environment, experiment, track, carModel, driver,
   session_id, lap, iCurrentTime, normalizedCarPosition)`` with
   ``WHERE iCurrentTime > 0 AND lap > 0`` — **no GROUP BY / MIN / CTE**
   (``feedback_quixlake_no_cte`` / ``feedback_quixlake_aggregation_slow``). The
   same rows carry the position samples, so no separate gate-samples round-trip is
   needed.
2. Python reduction: per ``(env, exp, track, car, driver, session_id, lap)`` track
   ``MAX(iCurrentTime)`` (the lap time) and the position samples; pick each
   driver's fastest **complete** lap (coverage >= ``_PARTIAL_LAP_MAX_POS``);
   reconstruct that lap's gate vector via the shared
   :func:`leaderboard_service_state.gate_vector.gate_vector_from_samples`.

The reduced per-driver records are produced as per-experiment ``type="seed"``
messages and folded into State in-context by the stateful SDF. Never raises: a
lake failure logs a WARNING and returns empty so a slow/broken lake never breaks
the pipeline. No SQL persistence and no other database — the lake is read-only.
"""

from __future__ import annotations

import logging
from typing import Any

from .gate_vector import gate_vector_from_samples
from .lakehouse_client import LakehouseClient
from .settings import Settings
from .state_model import INT_MAX, fold_best_lap

logger = logging.getLogger(__name__)

# Lap-completeness threshold: a lap whose samples cover < this fraction of the
# track is a quit/crash/timeout, dropped. Matches leaderboard_real
# ``_PARTIAL_LAP_MAX_POS``.
_PARTIAL_LAP_MAX_POS = 0.95

# A reduced per-driver record before it becomes a seed row.
# {(env, exp, track, car, driver): {"best_lap_ms", "best_lap_number",
#  "gate_vector"}}
ReducedRecords = dict[tuple[str, str, str, str, str], dict[str, Any]]


def build_seed_scan_sql(settings: Settings) -> str:
    """One full-table raw scan for the cold-start seed (byox-safe).

    Identifiers are validated at settings load time, so inlining is safe.
    No ``GROUP BY``, no ``MIN(...)``, no CTE.
    """
    table = settings.lake_table
    cur = settings.col_current_time
    pos = settings.col_normalized_position
    return (
        f"SELECT environment, experiment, track, carModel, driver, "
        f"session_id, lap, {cur}, {pos} "
        f"FROM {table} "
        f"WHERE {cur} > 0 AND lap > 0"
    )


def reduce_seed_rows(
    rows: list[dict[str, Any]],
    settings: Settings,
) -> ReducedRecords:
    """Reduce raw-scan rows to per-driver fastest-complete-lap gate records.

    Buckets samples by ``(env, exp, track, car, driver, session_id, lap)``, takes
    ``MAX(iCurrentTime)`` as the lap time, drops partial laps, picks each driver's
    fastest complete lap, and reconstructs its gate vector via the shared helper.
    """
    cur_col = settings.col_current_time
    pos_col = settings.col_normalized_position
    gate_count = settings.gate_count

    # (env, exp, track, car, driver, session, lap) -> list[(pos, ts)]
    buckets: dict[tuple[str, str, str, str, str, str, int], list[tuple[float, int]]] = {}
    for row in rows:
        driver = str(row.get("driver") or "").strip()
        if not driver:
            continue
        try:
            lap = int(row.get("lap") or 0)
            ts = int(float(row.get(cur_col) or 0))
            pos = float(row.get(pos_col) or 0.0)
        except (TypeError, ValueError):
            continue
        if lap <= 0 or ts <= 0:
            continue
        env = str(row.get("environment") or "").strip()
        exp = str(row.get("experiment") or "").strip()
        track = str(row.get("track") or "").strip()
        car = str(row.get("carModel") or "").strip()
        session = str(row.get("session_id") or "")
        if not (track and car):
            continue
        buckets.setdefault(
            (env, exp, track, car, driver, session, lap), []
        ).append((pos, ts))

    # Per (env, exp, track, car, driver, session, lap): lap_ms + max_pos.
    out: ReducedRecords = {}
    for (env, exp, track, car, driver, _session, lap), samples in buckets.items():
        if not samples:
            continue
        lap_ms = max(ts for _pos, ts in samples)
        max_pos = max(pos for pos, _ts in samples)
        if lap_ms <= 0 or lap_ms >= INT_MAX:
            continue
        if max_pos < _PARTIAL_LAP_MAX_POS:
            continue
        group_key = (env, exp, track, car, driver)
        prev = out.get(group_key)
        if prev is not None and int(prev["best_lap_ms"]) <= lap_ms:
            continue
        ordered = sorted(samples, key=lambda s: s[1])
        gate_vector = gate_vector_from_samples(ordered, gate_count)
        out[group_key] = {
            "best_lap_ms": int(lap_ms),
            "best_lap_number": int(lap),
            "gate_vector": gate_vector,
        }
    return out


def group_reduced_by_experiment(
    reduced: ReducedRecords,
) -> dict[str, dict[str, Any]]:
    """Group reduced records into per-experiment seed payloads.

    Output ``{experiment: {"environment": env, "rows": [{track, carModel, driver,
    best_lap_ms, best_lap_number, gate_vector}, ...]}}``. Blank experiments are
    skipped; ``environment`` is the last non-blank env seen for the experiment.
    Pure — unit-testable with a plain dict.
    """
    out: dict[str, dict[str, Any]] = {}
    for (env, exp, track, car, driver), record in reduced.items():
        if not exp:
            continue
        bucket = out.setdefault(exp, {"environment": "", "rows": []})
        if env and not bucket["environment"]:
            bucket["environment"] = env
        bucket["rows"].append(
            {
                "track": track,
                "carModel": car,
                "driver": driver,
                "best_lap_ms": int(record["best_lap_ms"]),
                "best_lap_number": int(record["best_lap_number"]),
                "gate_vector": list(record["gate_vector"]),
            }
        )
    return out


def build_seed_messages(reduced: ReducedRecords) -> list[dict[str, Any]]:
    """Turn grouped records into per-experiment ``{"type":"seed", ...}`` dicts."""
    grouped = group_reduced_by_experiment(reduced)
    return [
        {
            "type": "seed",
            "experiment": experiment,
            "environment": payload["environment"],
            "rows": payload["rows"],
        }
        for experiment, payload in grouped.items()
    ]


def query_and_reduce(settings: Settings) -> ReducedRecords:
    """Run the one-time seed scan and reduce it. Never raises.

    Returns an empty dict on missing lake URL, lake failure, or empty result.
    """
    url = settings.lakehouse_query_url
    if not url:
        logger.warning(
            "seed skipped: no Lakehouse Query URL configured "
            "(Quix__Lakehouse__Query__Url / LAKE_API_URL)"
        )
        return {}
    sql = build_seed_scan_sql(settings)
    logger.info("seed: one-time lake scan SQL: %s", sql)
    try:
        client = LakehouseClient(url, settings.lakehouse_query_token)
        df = client.query(sql)
    except Exception as exc:  # noqa: BLE001 — never break the pipeline
        logger.warning("seed lake query failed (%s); state unchanged", exc)
        return {}
    if df.empty:
        logger.info("seed: lake scan returned 0 rows")
        return {}
    df = df.fillna("")
    reduced = reduce_seed_rows(df.to_dict("records"), settings)
    logger.info("seed: reduced to %d per-driver best-lap records", len(reduced))
    return reduced


def seed_experiment_payload(
    settings: Settings,
    experiment: str,
    payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Fold lakehouse bests for *experiment* into *payload* (cold-start only).

    Runs the byox-safe scan, reduces in Python, and folds **only the records whose
    experiment matches** the target key into the nested State payload via
    :func:`leaderboard_service_state.state_model.fold_best_lap`. Returns
    ``(payload, changed)``. Never raises; a lake failure returns the input
    unchanged. This is the lazy in-context seed for a ``type="read"`` trigger that
    hits an empty experiment.
    """
    reduced = query_and_reduce(settings)
    if not reduced:
        return (payload or {}), False

    result = dict(payload) if payload else {}
    folded = 0
    for (env, exp, track, car, driver), record in reduced.items():
        if exp != experiment:
            continue
        result, changed = fold_best_lap(
            result,
            track,
            car,
            driver,
            int(record["best_lap_ms"]),
            list(record["gate_vector"]),
            int(record["best_lap_number"]),
            environment=env,
        )
        if changed:
            folded += 1
    logger.info(
        "cold-start seed for experiment=%s: %d records folded into State",
        experiment,
        folded,
    )
    return result, folded > 0
