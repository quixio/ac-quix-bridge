"""Cold-start lakehouse seed, run **inside the processing context**.

The authoritative source of best laps is the live topic stream; the lakehouse is
queried **only to seed a genuinely empty State payload** for an experiment — e.g.
a fresh consumer group / wiped state volume. Because QuixStreams' native State is
reachable only while processing a message for a given key, the seed cannot run on
a background thread: it runs lazily, the first time the stateful read branch
processes a trigger for an experiment whose ``state.get(experiment)`` is empty.

The seed issues the same byox-safe raw scan as before (no ``GROUP BY`` / ``MIN``
/ CTE — ``feedback_quixlake_no_cte`` / ``feedback_quixlake_aggregation_slow``),
reduces it to per-``(group, driver)`` minima in Python, and folds the rows **for
the target experiment only** into the nested State payload via
:func:`best_laps_cache.state_model.fold_lap`. A failed seed logs a WARNING and
returns the payload unchanged, so a slow/broken lake never breaks the pipeline.

No SQL persistence and no other database: the lake is a read-only source; the
reduced rows land directly in RocksDB State via the pure fold helper.
"""

from __future__ import annotations

import logging
from typing import Any

from .lakehouse_client import LakehouseClient
from .settings import Settings
from .state_model import INT_MAX, fold_lap

logger = logging.getLogger(__name__)


def build_reconcile_sql(
    lake_table: str, best_col: str, valid_laps_only: bool = True
) -> str:
    """Full-table raw scan — partition keys + best time, positives only.

    When *valid_laps_only* is ``True`` (default) the query reads ``best_col``
    (``iBestTime``) directly — byox-safe: no ``GROUP BY``, no ``MIN``, no CTE.

    When *valid_laps_only* is ``False`` the query aggregates ``iLastTime`` per
    group (the last completed lap time, valid or not) and aliases it as
    ``iBestTime`` so ``reduce_rows`` and ``fold_lap`` need no changes.
    Identifiers are validated at settings load time, so inlining is safe.
    """
    if valid_laps_only:
        return (
            f"SELECT environment, experiment, track, carModel, driver, {best_col} "
            f"FROM {lake_table} "
            f"WHERE {best_col} > 0 AND {best_col} < {INT_MAX}"
        )
    return (
        f"SELECT environment, experiment, track, carModel, driver, "
        f"MIN(iLastTime) AS iBestTime "
        f"FROM {lake_table} "
        f"WHERE iLastTime > 0 AND iLastTime < {INT_MAX} "
        f"GROUP BY environment, experiment, track, carModel, driver"
    )


def reduce_rows(
    rows: list[dict[str, Any]], best_col: str
) -> dict[tuple[str, str, str, str, str], int]:
    """Reduce raw-scan rows to ``{(env, exp, track, car, driver): min_ms}``.

    Drops non-positive / INT_MAX / blank-driver rows. The key is the five-tuple
    of partition fields (not an encoded string) so the seed can fold per
    experiment without re-parsing.
    """
    out: dict[tuple[str, str, str, str, str], int] = {}
    for row in rows:
        driver = str(row.get("driver") or "").strip()
        if not driver:
            continue
        raw_best = row.get(best_col)
        if raw_best is None or raw_best == "":
            continue
        try:
            best_ms = int(float(raw_best))
        except (TypeError, ValueError):
            continue
        if best_ms <= 0 or best_ms >= INT_MAX:
            continue
        key = (
            str(row.get("environment") or "").strip(),
            str(row.get("experiment") or "").strip(),
            str(row.get("track") or "").strip(),
            str(row.get("carModel") or "").strip(),
            driver,
        )
        prev = out.get(key)
        if prev is None or best_ms < prev:
            out[key] = best_ms
    return out


def seed_experiment_payload(
    settings: Settings,
    experiment: str,
    payload: dict[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Fold lakehouse bests for *experiment* into *payload* (cold-start only).

    Returns ``(payload, changed)``. Runs the byox-safe raw scan, reduces in
    Python, and folds **only the rows whose experiment matches** the target key.
    Never raises; a lake failure logs a WARNING and returns the input unchanged.
    """
    url = settings.lakehouse_query_url
    if not url:
        logger.warning(
            "lakehouse seed skipped for experiment=%s: no Lakehouse Query URL "
            "configured (Quix__Lakehouse__Query__Url / LAKE_API_URL)",
            experiment,
        )
        return (payload or {}), False

    sql = build_reconcile_sql(settings.lake_table, settings.col_best_time, settings.valid_laps_only)
    logger.info("cold-start seed for experiment=%s — lake scan SQL: %s", experiment, sql)
    try:
        client = LakehouseClient(url, settings.lakehouse_query_token)
        df = client.query(sql)
    except Exception as exc:  # noqa: BLE001 — never break the pipeline
        logger.warning(
            "lakehouse seed failed for experiment=%s (%s); state unchanged",
            experiment,
            exc,
        )
        return (payload or {}), False

    if df.empty:
        logger.info("lakehouse seed for experiment=%s returned 0 rows", experiment)
        return (payload or {}), False

    df = df.fillna("")
    reduced = reduce_rows(df.to_dict("records"), settings.col_best_time)

    result = dict(payload) if payload else {}
    folded = 0
    for (env, exp, track, car, driver), best_ms in reduced.items():
        if exp != experiment:
            continue
        result, changed = fold_lap(
            result, track, car, driver, best_ms, environment=env
        )
        if changed:
            folded += 1
    logger.info(
        "cold-start seed for experiment=%s: %d lake groups, %d folded into State",
        experiment,
        len(reduced),
        folded,
    )
    return result, folded > 0
