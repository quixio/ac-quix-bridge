"""Proactive cold-start lakehouse seed, run **once at boot** on a worker thread.

QuixStreams' native State (RocksDB) is writable only inside the stateful SDF
processing context, scoped to the current message key — so the boot seeder does
NOT touch RocksDB directly. It drives the existing SDF (mirrors
``best-laps-cache/best_laps_cache/boot_seed.py``):

1. **Gate on State emptiness via a durable marker file** at
   ``<Quix__State__Dir>/.seeded``. Present -> State retained/already seeded ->
   skip. Absent -> proceed. A single flag file, not a queryable store — no DB.
2. **Query the lakehouse ONCE** with the byox-safe raw scan and reduce per lap to
   per-driver gate-vector records (:mod:`leaderboard_service_state.seed`).
3. **Group by experiment** and **produce one ``{"type":"seed", ...}`` message per
   experiment** to the internal ``leaderboard-events`` topic, keyed by experiment.
4. The stateful SDF folds each seed message in-context (the actual RocksDB write),
   idempotently (no clobber if already populated).
5. **Write the marker file** so a restart with a retained State volume re-queries
   nothing.

A lake failure or empty result logs a WARNING and leaves the marker unwritten, so
the pipeline still falls back to the lazy in-context seed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from .seed import build_seed_messages, query_and_reduce
from .settings import Settings

logger = logging.getLogger(__name__)

MARKER_FILENAME = ".seeded"


def marker_path(settings: Settings) -> str:
    return os.path.join(settings.state_dir, MARKER_FILENAME)


def marker_exists(settings: Settings) -> bool:
    return os.path.isfile(marker_path(settings))


def write_marker(settings: Settings) -> None:
    path = marker_path(settings)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("seeded\n")
    logger.info("boot-seed marker written: %s", path)


def run_boot_seed(
    settings: Settings,
    produce_seed: Callable[[str, dict[str, Any]], None],
) -> bool:
    """Proactively seed State once at boot, driving writes through the SDF.

    *produce_seed* is ``(experiment_key, message_dict) -> None`` supplied by the
    pipeline so this module never touches Kafka serializers directly. Returns
    ``True`` if a seed actually ran and the marker was written, ``False`` if
    skipped (marker present / no lake URL / empty result / failure). Never raises.
    """
    if marker_exists(settings):
        logger.info(
            "boot-seed skipped: marker present (%s) — State retained/already seeded",
            marker_path(settings),
        )
        return False

    reduced = query_and_reduce(settings)
    if not reduced:
        logger.info(
            "boot-seed: no records to seed; marker NOT written so a later boot "
            "can retry once data exists"
        )
        return False

    messages = build_seed_messages(reduced)
    if not messages:
        logger.info("boot-seed: no experiment-keyed rows after reduction; skipping")
        return False

    for message in messages:
        produce_seed(message["experiment"], message)
    logger.info(
        "boot-seed: produced %d per-experiment seed message(s) to %s (%d records)",
        len(messages),
        settings.events_topic,
        len(reduced),
    )

    write_marker(settings)
    return True
