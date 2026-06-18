"""Proactive cold-start lakehouse seed, run **once at boot** on a worker thread.

Problem this fixes
------------------
QuixStreams' native State (RocksDB) is writable **only inside the stateful SDF
processing context, scoped to the current message key**. The in-context seed in
:mod:`best_laps_cache.seed` therefore runs lazily — only when a ``type="read"``
trigger arrives for an experiment whose State is empty. On a *fresh deploy* no
such trigger exists until live session/config traffic arrives, so the board sits
empty until someone drives. This module seeds proactively at boot instead.

How it works (within the State-only constraint)
-----------------------------------------------
Because State cannot be written from the main/worker thread, the boot seeder does
NOT touch RocksDB directly. It drives the existing SDF:

1. **Gate on State emptiness via a durable marker file.** State cannot be probed
   outside the processing context, so the most reliable available signal is a
   flag file at ``<Quix__State__Dir>/.seeded``. Present → State is already
   populated (or the volume was retained) → skip. Absent → proceed. The marker is
   a single flag *file*, not a queryable store — no database is introduced.
2. **Query the lakehouse ONCE** with the existing byox-safe full Arrow scan
   (:func:`best_laps_cache.seed.build_reconcile_sql` +
   :class:`best_laps_cache.lakehouse_client.LakehouseClient` +
   :func:`best_laps_cache.seed.reduce_rows`).
3. **Group the reduced bests by experiment** (:func:`group_reduced_by_experiment`)
   and, for each experiment, **produce one ``{"type":"seed", ...}`` message** to
   the internal ``best-laps-events`` topic, keyed by that experiment — using the
   same producer/serializers the pipeline already uses for that topic.
4. The stateful SDF (``pipeline._handle_event``) consumes each seed message
   in-context for its experiment key and folds the carried rows into
   ``state[experiment]`` — the actual RocksDB write, idempotently (no clobber if
   already populated). That keeps every State write inside the SDF context.
5. After all seed messages are produced, **write the marker file** so a restart
   with a retained State volume re-queries nothing.

A lake failure or empty result logs a WARNING and leaves the marker unwritten, so
the pipeline still falls back to the existing in-context lazy seed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from .lakehouse_client import LakehouseClient
from .seed import build_reconcile_sql, reduce_rows
from .settings import Settings

logger = logging.getLogger(__name__)

MARKER_FILENAME = ".seeded"

# Carried in every boot message so the stateful handler can recognise it.
SEED_EVENT_TYPE = "seed"


def marker_path(settings: Settings) -> str:
    """Absolute path of the durable "already seeded" flag file."""
    return os.path.join(settings.state_dir, MARKER_FILENAME)


def marker_exists(settings: Settings) -> bool:
    """True when the boot seed has already run for this State volume."""
    return os.path.isfile(marker_path(settings))


def write_marker(settings: Settings) -> None:
    """Create the durable flag file (best-effort; dirs created if needed)."""
    path = marker_path(settings)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("seeded\n")
    logger.info("boot-seed marker written: %s", path)


def group_reduced_by_experiment(
    reduced: dict[tuple[str, str, str, str, str], int],
) -> dict[str, dict[str, Any]]:
    """Group ``reduce_rows`` output into per-experiment seed payloads.

    Input key is the five-tuple ``(env, exp, track, car, driver)`` → ``best_ms``
    (see :func:`best_laps_cache.seed.reduce_rows`). Output is::

        {experiment: {"environment": env, "rows": [
            {"track", "carModel", "driver", "best_lap_ms"}, ...
        ]}}

    Pure function: no Kafka, no State, so it is unit-testable with a plain dict.
    Experiments are skipped when blank; the per-experiment ``environment`` is the
    last non-blank env seen for that experiment (constant per experiment here).
    """
    out: dict[str, dict[str, Any]] = {}
    for (env, exp, track, car, driver), best_ms in reduced.items():
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
                "best_lap_ms": int(best_ms),
            }
        )
    return out


def build_seed_messages(
    reduced: dict[tuple[str, str, str, str, str], int],
) -> list[dict[str, Any]]:
    """Turn the grouped bests into ``{"type":"seed", ...}`` event dicts.

    One message per experiment, shaped to ride the existing ``best-laps-events``
    JSON contract and be folded in-context by ``pipeline._handle_event``.
    """
    grouped = group_reduced_by_experiment(reduced)
    return [
        {
            "type": SEED_EVENT_TYPE,
            "experiment": experiment,
            "environment": payload["environment"],
            "rows": payload["rows"],
        }
        for experiment, payload in grouped.items()
    ]


def run_boot_seed(
    settings: Settings,
    produce_seed: Callable[[str, dict[str, Any]], None],
) -> bool:
    """Proactively seed State once at boot, driving writes through the SDF.

    *produce_seed* is ``(experiment_key, message_dict) -> None`` — the caller
    (the pipeline) supplies it so this module never touches Kafka serializers
    directly. Returns ``True`` if a seed actually ran and the marker was written,
    ``False`` if skipped (marker present / no lake URL / empty result / failure).
    Never raises: any error logs a WARNING and falls back to the lazy in-context
    seed, leaving the marker unwritten so a later boot can retry.
    """
    if marker_exists(settings):
        logger.info(
            "boot-seed skipped: marker present (%s) — State retained/already seeded",
            marker_path(settings),
        )
        return False

    url = settings.lakehouse_query_url
    if not url:
        logger.warning(
            "boot-seed skipped: no Lakehouse Query URL configured "
            "(Quix__Lakehouse__Query__Url / LAKE_API_URL); lazy in-context seed "
            "remains the fallback"
        )
        return False

    sql = build_reconcile_sql(settings.lake_table, settings.col_best_time)
    logger.info("boot-seed: one-time lake scan SQL: %s", sql)
    try:
        client = LakehouseClient(url, settings.lakehouse_query_token)
        df = client.query(sql)
    except Exception as exc:  # noqa: BLE001 — never break startup
        logger.warning(
            "boot-seed lake query failed (%s); marker NOT written, lazy seed "
            "remains the fallback",
            exc,
        )
        return False

    if df.empty:
        logger.info(
            "boot-seed: lake scan returned 0 rows; marker NOT written so a later "
            "boot can retry once data exists"
        )
        return False

    df = df.fillna("")
    reduced = reduce_rows(df.to_dict("records"), settings.col_best_time)
    messages = build_seed_messages(reduced)
    if not messages:
        logger.info("boot-seed: no experiment-keyed rows after reduction; skipping")
        return False

    for message in messages:
        produce_seed(message["experiment"], message)
    logger.info(
        "boot-seed: produced %d per-experiment seed message(s) to the events topic "
        "(%d lake groups)",
        len(messages),
        len(reduced),
    )

    write_marker(settings)
    return True
