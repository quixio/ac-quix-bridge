"""Periodic, strictly-serialized full-table reconcile (cold path).

A single daemon thread sleeps ``RECONCILE_INTERVAL_S`` between cycles. Each
cycle issues **one** whole-table raw-scan query, waits for the full CSV
response, reduces it to per-``(group, driver)`` minima in Python, and merges
into the store with ``min(state, db)`` (O4 — a live-set faster lap is never
clobbered).

Serialization: a single worker thread runs cycles one at a time, and a
``threading.Lock`` acquired non-blocking guards the actual query so an
externally-triggered cycle (e.g. the cold-start kick) can never overlap the
timer's cycle. Two scans are never in flight at once; a slow scan on a fast
timer simply means the next tick finds the lock held and no-ops.

The reconcile SQL is the byox-safe shape: no ``GROUP BY``, no ``MIN(...)``,
no CTE (``feedback_quixlake_no_cte`` / ``feedback_quixlake_aggregation_slow``).
A failed cycle logs a WARNING and leaves the store untouched.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .lakehouse_client import LakehouseClient
from .settings import Settings
from .store import BEST_TIME_SENTINEL, BestLapsStore, make_key

logger = logging.getLogger(__name__)


def build_reconcile_sql(lake_table: str, best_col: str) -> str:
    """One full-table raw scan — partition keys + best time, positives only.

    Identifiers are validated at settings load time, so inlining is safe.
    """
    return (
        f"SELECT environment, experiment, track, carModel, driver, {best_col} "
        f"FROM {lake_table} "
        f"WHERE {best_col} > 0 AND {best_col} < {BEST_TIME_SENTINEL}"
    )


def reduce_rows(rows: list[dict[str, Any]], best_col: str) -> dict[str, int]:
    """Reduce raw-scan rows to ``{store_key: min_best_lap_ms}`` per group.

    Drops ``best_lap_ms <= 0`` and blank-driver rows (matching the
    leaderboard's ``test_best_laps_raw_scan`` contract).
    """
    out: dict[str, int] = {}
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
        if best_ms <= 0 or best_ms >= BEST_TIME_SENTINEL:
            continue
        key = make_key(
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


class ReconcileWorker:
    """Daemon thread that periodically reconciles the store against the lake."""

    def __init__(self, settings: Settings, store: BestLapsStore) -> None:
        self._settings = settings
        self._store = store
        self._stop = threading.Event()
        self._cycle_lock = threading.Lock()  # serialize: never two scans at once
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._loop, name="reconcile-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _loop(self) -> None:
        # Kick one cycle immediately on boot (cold-start seed), then on the
        # interval. `Event.wait` returns True when stopped, so the loop exits
        # promptly on shutdown.
        self.run_cycle()
        while not self._stop.wait(self._settings.reconcile_interval_s):
            self.run_cycle()

    def run_cycle(self) -> int:
        """Run one reconcile cycle. Returns the number of keys changed (0 on
        a no-op, a skipped overlap, or a failure). Never raises."""
        if not self._cycle_lock.acquire(blocking=False):
            logger.info("reconcile already running; skipping this tick")
            return 0
        try:
            return self._run_locked()
        finally:
            self._cycle_lock.release()

    def _run_locked(self) -> int:
        url = self._settings.lakehouse_query_url
        if not url:
            logger.warning(
                "reconcile skipped: no Lakehouse Query URL configured "
                "(Quix__Lakehouse__Query__Url / LAKE_API_URL)"
            )
            return 0
        sql = build_reconcile_sql(
            self._settings.lake_table, self._settings.col_best_time
        )
        logger.info("reconcile scan SQL: %s", sql)
        # Clean the INT_MAX stub out of existing state in parallel with the
        # SQL-side filter on the incoming Lakehouse rows — every cycle, even if
        # the query below returns nothing or fails.
        purged = self._store.purge_sentinels()
        if purged:
            logger.info("reconcile purged %d INT_MAX stub rows from state", purged)
        try:
            client = LakehouseClient(url, self._settings.lakehouse_query_token)
            df = client.query(sql)
        except Exception as exc:  # noqa: BLE001 — never break the loop
            logger.warning("reconcile cycle failed (%s); state unchanged", exc)
            return 0
        if df.empty:
            logger.info("reconcile returned 0 rows; state unchanged")
            return 0
        df = df.fillna("")
        rows: list[dict[str, Any]] = df.to_dict("records")
        reconciled = reduce_rows(rows, self._settings.col_best_time)
        changed = self._store.merge_reconcile(reconciled)
        logger.info(
            "reconcile merged: %d groups scanned, %d keys changed, %d total",
            len(reconciled),
            changed,
            len(self._store),
        )
        return changed
