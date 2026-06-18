"""Cold-start lakehouse seed (cold path).

The authoritative source of best laps is the live topic stream (raw + session
+ DCM); the lakehouse is queried **only to seed an empty store** — e.g. on a
fresh consumer group with no rebuilt state yet. A single daemon thread checks
on each ``RECONCILE_INTERVAL_S`` tick: while the store is empty it issues
**one** whole-table raw-scan query, reduces it to per-``(group, driver)``
minima in Python, and merges into the store with ``min(state, db)`` (a
live-set faster lap is never clobbered). The instant the store holds any lap
(from a successful seed or from the topic) every subsequent tick is a no-op
and the lakehouse is never queried again.

Serialization: a single worker thread runs cycles one at a time, and a
``threading.Lock`` acquired non-blocking guards the query so two scans are
never in flight at once; a slow scan on a fast timer simply finds the lock
held and no-ops.

The seed SQL is the byox-safe shape: no ``GROUP BY``, no ``MIN(...)``, no CTE
(``feedback_quixlake_no_cte`` / ``feedback_quixlake_aggregation_slow``). A
failed seed logs a WARNING and leaves the store untouched.
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
        # Seed once on boot, then re-check on the interval. Each cycle is a
        # no-op unless the store is empty (see _run_locked), so the interval
        # acts only as a retry while no state has been built yet — as soon as
        # topics/DCM (or a successful seed) populate the store, the lakehouse
        # is never queried again. `Event.wait` returns True when stopped.
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
        # Lakehouse is a COLD-START SEED ONLY: query it solely while the store
        # is empty (e.g. a fresh consumer group with no rebuilt state yet).
        # Once topics + DCM have put any best lap into the store, the live
        # stream is authoritative and we never query the lake again.
        if len(self._store) > 0:
            logger.debug("state non-empty; skipping lakehouse seed query")
            return 0
        url = self._settings.lakehouse_query_url
        if not url:
            logger.warning(
                "lakehouse seed skipped: no Lakehouse Query URL configured "
                "(Quix__Lakehouse__Query__Url / LAKE_API_URL)"
            )
            return 0
        sql = build_reconcile_sql(
            self._settings.lake_table, self._settings.col_best_time
        )
        logger.info("state empty — lakehouse seed scan SQL: %s", sql)
        try:
            client = LakehouseClient(url, self._settings.lakehouse_query_token)
            df = client.query(sql)
        except Exception as exc:  # noqa: BLE001 — never break the loop
            logger.warning("lakehouse seed failed (%s); state unchanged", exc)
            return 0
        if df.empty:
            logger.info("lakehouse seed returned 0 rows; state still empty")
            return 0
        df = df.fillna("")
        rows: list[dict[str, Any]] = df.to_dict("records")
        reconciled = reduce_rows(rows, self._settings.col_best_time)
        changed = self._store.merge_reconcile(reconciled)
        logger.info(
            "lakehouse seed: %d groups scanned, %d keys seeded, %d total",
            len(reconciled),
            changed,
            len(self._store),
        )
        return changed
