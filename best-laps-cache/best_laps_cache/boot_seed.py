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

1. **Gate on a State-native ``seeded`` flag via the request bridge.** State cannot
   be probed outside the processing context, so the boot thread produces a
   ``{"type":"seed_gate", ...}`` event keyed by :data:`GATE_KEY` and waits (bounded
   timeout) on the :class:`~best_laps_cache.request_bridge.PendingRequests` bridge.
   The stateful SDF reads ``state.get("seeded")`` in-context and delivers the bool
   back, correlated by ``req_id``. Flag set → State already populated → skip. Flag
   absent → proceed. A timeout also proceeds (the fold is idempotent). The flag
   lives in the consumer-group/state-dir-scoped RocksDB store, so changing
   ``CONSUMER_GROUP`` / ``Quix__State__Dir`` wipes it and triggers a fresh reseed.
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
5. After all seed messages are produced, **produce a ``{"type":"mark_seeded", ...}``
   event keyed by :data:`GATE_KEY`** so the SDF sets ``state.set("seeded", True)``
   in-context; a restart with a retained State volume then re-queries nothing.

A lake failure or empty result logs a WARNING and leaves the flag unset, so the
pipeline still falls back to the existing in-context lazy seed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .lakehouse_client import LakehouseClient
from .seed import build_reconcile_sql, reduce_rows
from .settings import Settings

logger = logging.getLogger(__name__)

# Reserved State key the gate events co-partition onto, so the read (seed_gate),
# the write (mark_seeded) and any future gate touch all land on one consistent
# RocksDB store partition. Defined HERE (not pipeline.py) to avoid an import
# cycle: pipeline.py already imports from boot_seed.py (spec §8 resolved risk).
GATE_KEY = "__boot_seed_gate__"

# Sub-key under GATE_KEY's State context that holds the seeded flag.
_GATE_FLAG = "seeded"

# Carried in every boot message so the stateful handler can recognise it.
SEED_EVENT_TYPE = "seed"


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
    produce_event: Callable[[str, dict[str, Any]], None],
    pending: Any,
) -> bool:
    """Proactively seed State once at boot, driving writes through the SDF.

    *produce_event* is ``(key, message_dict) -> None`` — the caller (the pipeline)
    supplies it so this module never touches Kafka serializers directly. *pending*
    is the :class:`~best_laps_cache.request_bridge.PendingRequests` bridge, used to
    round-trip the State-native seeded-flag read through the SDF.

    Gate: produce a ``seed_gate`` event keyed :data:`GATE_KEY` and wait on the
    bridge. If the flag is already set → skip (``return False``). Flag absent OR
    gate timeout → proceed with the one-time lake query. A successful seed produces
    a ``mark_seeded`` event so the SDF sets the flag in-context.

    Returns ``True`` if a seed actually ran (and ``mark_seeded`` was produced),
    ``False`` if skipped (flag set / no lake URL / empty result / failure). Never
    raises: any error logs a WARNING and falls back to the lazy in-context seed,
    leaving the flag unset so a later boot can retry.
    """
    try:
        req_id = pending.open()
        produce_event(
            GATE_KEY,
            {"type": "seed_gate", "experiment": GATE_KEY, "req_id": req_id},
        )
        delivered, payload = pending.wait(req_id, settings.boot_seed_gate_timeout_s)
        if delivered and payload and payload.get("seeded"):
            logger.info("boot-seed skipped: State flag set")
            return False
        if not delivered:
            # Proceeding on a timeout is safe: the in-context ``seed`` fold is
            # idempotent (pipeline._handle_event never clobbers a populated
            # experiment) and a successful seed produces ``mark_seeded``. Favors a
            # populated board over a wrongly-skipped empty one.
            logger.info("boot-seed gate timed out → proceeding (idempotent)")
        else:
            logger.info("boot-seed: State flag absent → querying lake")

        url = settings.lakehouse_query_url
        if not url:
            logger.warning(
                "boot-seed skipped: no Lakehouse Query URL configured "
                "(Quix__Lakehouse__Query__Url / LAKE_API_URL); lazy in-context "
                "seed remains the fallback"
            )
            return False

        sql = build_reconcile_sql(settings.lake_table, settings.col_best_time, settings.valid_laps_only)
        logger.info("boot-seed: one-time lake scan SQL: %s", sql)
        try:
            client = LakehouseClient(url, settings.lakehouse_query_token)
            df = client.query(sql)
        except Exception as exc:  # noqa: BLE001 — never break startup
            logger.warning(
                "boot-seed lake query failed (%s); flag NOT set, lazy seed "
                "remains the fallback",
                exc,
            )
            return False

        if df.empty:
            logger.info(
                "boot-seed: lake scan returned 0 rows; flag NOT set so a later "
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
            produce_event(message["experiment"], message)
        logger.info(
            "boot-seed: produced %d per-experiment seed message(s) to the events "
            "topic (%d lake groups)",
            len(messages),
            len(reduced),
        )

        produce_event(GATE_KEY, {"type": "mark_seeded", "experiment": GATE_KEY})
        logger.info("boot-seed: mark_seeded produced")
        return True
    except Exception:  # noqa: BLE001 — boot seed must never crash startup
        logger.warning("boot-seed failed; lazy in-context seed remains", exc_info=True)
        return False
