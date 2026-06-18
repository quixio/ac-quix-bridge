"""Best-laps store: the queryable per-group best-lap index.

Two writers, one reader:

* **Raw consumer (hot path)** writes via :meth:`BestLapsStore.update_live`
  inside the QuixStreams processing context. The authoritative durable copy
  lives in a **persistent QuixStreams State** store (RocksDB-backed) so a
  redeploy keeps warm live-derived bests until the next reconcile. Because
  QuixStreams State is only reachable from inside the consumer's processing
  context (it is partition-scoped and not thread-safe for arbitrary reads),
  every write is *also* mirrored into this module's in-memory index.
* **Reconcile worker (cold path)** writes via :meth:`merge_reconcile` —
  whole-table results merged with ``min(state, db)`` per group/driver.
* **HTTP API** reads via :meth:`query` — never touches QuixStreams State,
  only the in-memory mirror under a lock.

Key (the five Lakehouse partition keys, unit-separated):

    f"{environment}\\x1f{experiment}\\x1f{track}\\x1f{carModel}\\x1f{driver}"

``\\x1f`` (ASCII unit separator) cannot appear in any of the field values,
so the encoding is collision-free and round-trips losslessly. ``driver`` is
stored raw (lake string), not folded — the consumer folds if it needs to.

Value (JSON-serialisable dict):

    {"environment", "experiment", "track", "carModel", "driver",
     "best_lap_ms": int, "source": "live"|"reconcile", "updated_epoch": float}
"""

from __future__ import annotations

import threading
import time
from typing import Any, Iterable

KEY_SEP = "\x1f"

# Field order in the key — also the canonical column order the API echoes.
GROUP_FIELDS = ("environment", "experiment", "track", "carModel", "driver")

# AC reports iBestTime as INT_MAX (2**31 - 1) when no valid lap has been set.
# Treat that stub value — and anything at or above it — as "no lap", never a
# real best. Filtered on every write path and purged from state each reconcile.
BEST_TIME_SENTINEL = 2147483647


def make_key(
    environment: str, experiment: str, track: str, car_model: str, driver: str
) -> str:
    """Encode the five partition keys into one collision-free store key."""
    return KEY_SEP.join((environment, experiment, track, car_model, driver))


def split_key(key: str) -> dict[str, str]:
    """Inverse of :func:`make_key` — decode a store key to its five fields."""
    parts = key.split(KEY_SEP)
    # Pad defensively so a malformed key never raises (degenerate keys from
    # un-enriched raw can carry empty fields, which is fine).
    parts += [""] * (len(GROUP_FIELDS) - len(parts))
    return dict(zip(GROUP_FIELDS, parts))


def _value(
    environment: str,
    experiment: str,
    track: str,
    car_model: str,
    driver: str,
    best_lap_ms: int,
    source: str,
) -> dict[str, Any]:
    return {
        "environment": environment,
        "experiment": experiment,
        "track": track,
        "carModel": car_model,
        "driver": driver,
        "best_lap_ms": int(best_lap_ms),
        "source": source,
        "updated_epoch": time.time(),
    }


class BestLapsStore:
    """Thread-safe in-memory mirror of the best-laps index.

    The QuixStreams persistent State store is the durable copy written on the
    consumer thread; this mirror is what the reconcile worker and the HTTP
    API read/write concurrently. On consumer startup the persistent store's
    contents are loaded into the mirror via :meth:`seed_from_items` so warm
    data survives a restart and is queryable before the first reconcile.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = {}

    # -- seeding -----------------------------------------------------------

    def seed_from_items(self, items: Iterable[tuple[str, dict[str, Any]]]) -> int:
        """Load ``(key, value)`` pairs (e.g. from persistent State) into the
        mirror, keeping the faster value on any collision. Returns the count
        of keys present after seeding."""
        with self._lock:
            for key, value in items:
                if int(value.get("best_lap_ms", 0)) >= BEST_TIME_SENTINEL:
                    continue  # never seed the INT_MAX stub
                cur = self._index.get(key)
                if cur is None or int(value.get("best_lap_ms", 0)) < int(
                    cur.get("best_lap_ms", 0)
                ):
                    self._index[key] = dict(value)
            return len(self._index)

    # -- live (hot path) ---------------------------------------------------

    def update_live(
        self,
        environment: str,
        experiment: str,
        track: str,
        car_model: str,
        driver: str,
        best_lap_ms: int,
    ) -> dict[str, Any] | None:
        """Apply one raw-derived best lap (monotonic min).

        Returns the new value dict when the store changed (so the caller can
        also write it into the QuixStreams persistent State), else ``None``.
        ``best_lap_ms <= 0``, the INT_MAX stub, or a blank driver is a no-op.
        """
        if best_lap_ms <= 0 or best_lap_ms >= BEST_TIME_SENTINEL or not driver:
            return None
        key = make_key(environment, experiment, track, car_model, driver)
        with self._lock:
            cur = self._index.get(key)
            if cur is not None and int(cur.get("best_lap_ms", 0)) <= best_lap_ms:
                return None
            value = _value(
                environment, experiment, track, car_model, driver, best_lap_ms, "live"
            )
            self._index[key] = value
            return dict(value)

    # -- reconcile (cold path) --------------------------------------------

    def merge_reconcile(self, reconciled: dict[str, int]) -> int:
        """Merge a full reconcile result into the mirror with ``min`` policy.

        *reconciled* maps store-key → best_lap_ms (already reduced per
        ``(group, driver)`` in Python). For each key the kept value is
        ``min(existing_state, reconciled)`` so a live-set faster lap that the
        lake has not yet written is never clobbered by an older/slower DB
        value (O4). New keys (present in the DB, absent in State) are added.
        Returns the number of keys changed.
        """
        changed = 0
        with self._lock:
            for key, db_ms in reconciled.items():
                if db_ms <= 0 or db_ms >= BEST_TIME_SENTINEL:
                    continue
                cur = self._index.get(key)
                if cur is None:
                    fields = split_key(key)
                    self._index[key] = _value(
                        fields["environment"],
                        fields["experiment"],
                        fields["track"],
                        fields["carModel"],
                        fields["driver"],
                        db_ms,
                        "reconcile",
                    )
                    changed += 1
                elif db_ms < int(cur.get("best_lap_ms", 0)):
                    cur["best_lap_ms"] = db_ms
                    cur["source"] = "reconcile"
                    cur["updated_epoch"] = time.time()
                    changed += 1
        return changed

    def purge_sentinels(self) -> int:
        """Drop any stored entries whose best lap is the INT_MAX stub
        (``>= BEST_TIME_SENTINEL``). Returns the number removed.

        Called every reconcile cycle so stub rows are cleaned out of existing
        state in parallel with the incoming-Lakehouse-row filtering — covers
        any legacy/seeded entry written before the write-path guards existed.
        """
        with self._lock:
            doomed = [
                k
                for k, v in self._index.items()
                if int(v.get("best_lap_ms", 0)) >= BEST_TIME_SENTINEL
            ]
            for k in doomed:
                del self._index[k]
            return len(doomed)

    # -- read (HTTP API) ---------------------------------------------------

    def query(
        self,
        *,
        environment: str | None = None,
        experiment: str | None = None,
        track: str | None = None,
        car_model: str | None = None,
        driver: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return matching value dicts, filtered by any supplied dimensions.

        An absent (``None``) filter matches everything on that dimension.
        Filters are exact-match on the raw stored strings.
        """
        wanted = {
            "environment": environment,
            "experiment": experiment,
            "track": track,
            "carModel": car_model,
            "driver": driver,
        }
        out: list[dict[str, Any]] = []
        with self._lock:
            for value in self._index.values():
                if all(
                    want is None or value.get(field) == want
                    for field, want in wanted.items()
                ):
                    out.append(dict(value))
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._index)
