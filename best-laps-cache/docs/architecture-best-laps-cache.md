# Architecture — Best-Laps Cache Service

## What it does

`best-laps-cache` is a standalone QuixStreams service that maintains a
per-group **best-lap-time** index and exposes it over HTTP as a drop-in
substitute for the Lakehouse query the leaderboard currently runs. The index
is updated **live** from `ac-telemetry-raw` (AC's `iBestTime`, a monotonic
per-group/driver minimum) and **periodically reconciled** against the whole
Lakehouse table by a single strictly-serialized full-table scan. Consumers
during a live session hit this in-memory cache instead of the slow
partition-heavy Lakehouse (which times out at 30 s on full reads). Phase 1 is
the cache + API + reconcile only — the leaderboard is not rewired.

## Why this architecture (key decisions and trade-offs)

- **In-memory `BestLapsStore` as the single queryable truth.** Every accepted
  live write goes into a thread-safe `BestLapsStore` (`store.py`) that the
  API and reconcile worker read/write under a lock. `update_live` itself
  enforces the monotonic per-group/driver minimum, so the index is also the
  authority for "have we already seen a faster lap?" — there is no separate
  durable store to consult on the hot path. A redeploy starts cold; the first
  reconcile (kicked immediately on start) repopulates the mirror within one
  scan, so cold-start correctness is preserved. (An earlier design also wrote
  a parallel RocksDB QuixStreams State store for redeploy warmth, but its
  warm-read-on-boot hook was never implemented — the store was write-only and
  never fed the API — so it was removed alongside the SDF; see the consumer
  decision below.)

- **Manual consumer poll loop, not `Application.run()` (off-main-thread
  safety).** The consumer runs on a daemon thread while uvicorn owns the main
  thread. `Application.run()` installs `SIGINT`/`SIGTERM` handlers via
  `signal.signal` in `_setup_signal_handlers`, and `signal.signal` raises
  `ValueError: signal only works in main thread of the main interpreter` when
  called off the main thread — which crashed the deployed raw consumer. The
  fix replaces the three-SDF `app.run()` model with a manual loop over
  `app.get_consumer()` (a bare confluent consumer, no signal setup):
  `consumer.subscribe([raw, session, config])`, then
  `poll(timeout=0.5)` → deserialize with the matching `app.topic` →
  `_dispatch` by topic name to the same handlers the SDFs used. This mirrors
  the proven `leaderboard-service/api/live_telemetry.py:_consumer_loop`.
  Offsets auto-commit (`get_consumer(auto_commit_enable=True)` default),
  matching the SDF's commit cadence. Shutdown is the existing
  `threading.Event`: `stop()` sets it, the loop exits within one poll timeout,
  and the `with app.get_consumer()` block closes the consumer.

- **One serialized whole-table scan (O1).** The reconcile worker is a single
  daemon thread that runs cycles one at a time; a `threading.Lock` acquired
  non-blocking guards the actual query so an externally-triggered cycle can
  never overlap the timer's cycle. A slow scan on a fast timer simply means
  the next tick finds the lock held and no-ops. Never two scans in flight.

- **Raw scan + Python reduction, no server-side aggregation (byox rules).**
  The reconcile SQL is `SELECT environment, experiment, track, carModel,
  driver, iBestTime FROM <table> WHERE iBestTime > 0` — no `GROUP BY`, no
  `MIN(...)`, no CTE (`feedback_quixlake_no_cte`,
  `feedback_quixlake_aggregation_slow`). Per-`(group, driver)` minima are
  computed in Python (`reduce_rows`). The httpx client is built with
  `verify=False` for byox self-signed certs.

- **Merge policy = `min(live, db)` (O4).** On reconcile, a live-set faster
  lap that the lake has not yet written is never clobbered by an older/slower
  DB value. New keys present in the DB but absent in State are added.

- **CSV response (O5).** `GET /best-laps` returns `text/csv` in the exact
  column shape the Lakehouse `/query` returns for the leaderboard's best-laps
  scan (`environment, experiment, track, carModel, driver, iBestTime`), so a
  consumer can swap its Lakehouse query URL for this endpoint with zero
  parsing change. `?format=json` returns the row-envelope variant.

- **Session + DCM enrichment (O2 = yes).** `ac-telemetry-raw` carries no
  `track`/`carModel`/`driver`/`experiment`/`environment`
  (`feedback_ac_raw_payload_fields`). `enrichment.py` replicates the proven
  `leaderboard-service/api/live_telemetry.py` pattern: cache session metadata
  from `ac-telemetry-session`, resolve experiment/driver/environment from DCM
  (`/api/v1/configurations` → latest version content) on session-message
  arrival, and take the most-recent cache entry of each (the three topics use
  unrelated key namespaces, correct for the single-sim deployment). The
  reconcile path always has all five keys from the lake row.

## Data flow

```
RawConsumer._run: with app.get_consumer() as consumer → poll(0.5s) → _dispatch
ac-telemetry-raw ──► _process_raw
                         │  enrich(track,car,driver,exp,env) from caches
                         │  if iBestTime>0:
                         └──► BestLapsStore.update_live(...)  (min guard + write)
                                                                       │
ac-telemetry-session ──► Enrichment.handle_session_message ──► session cache + DCM fetch
ac-telemetry-config  ──► Enrichment.handle_config_event    ──► experiment cache refresh
                                                                       │
ReconcileWorker (daemon, every RECONCILE_INTERVAL_S, serialized) ──────┤
   one full-table scan ─► reduce_rows ─► BestLapsStore.merge_reconcile(min)
                                                                       │
GET /best-laps ──► BestLapsStore.query(filters) ──► CSV / JSON  ◄───────┘
```

Hot path (raw consumer) never issues a DB query. Cold path (reconcile) runs
on its own thread and never blocks raw consumption. HTTP reads only the
in-memory mirror.

## State schema

- **Store:** in-memory `BestLapsStore` (the single queryable index; no
  durable RocksDB State store).
- **Key:** the five Lakehouse partition keys joined with `\x1f` (ASCII unit
  separator, cannot appear in a value): `environment \x1f experiment \x1f
  track \x1f carModel \x1f driver`. `driver` stored raw (consumer folds).
- **Value (JSON):** `{environment, experiment, track, carModel, driver,
  best_lap_ms:int, source:"live"|"reconcile", updated_epoch:float}`.

## `GET /best-laps` contract

- **Filters** (all optional query params, exact-match, absent = no filter):
  `environment`, `experiment`, `track`, `carModel`, `driver`. Plus
  `format=csv` (default) | `json`.
- **CSV response** (`text/csv`): header
  `environment,experiment,track,carModel,driver,iBestTime`, one row per
  `(group, driver)`, sorted `(track, carModel, iBestTime)` so fastest is
  first within a group. `iBestTime` is the per-driver best in ms.
- **JSON response** (`format=json`): `{table, columns, rows[], row_count,
  source:"best-laps-cache", as_of_epoch}`.
- `GET /healthz` → `{status:"ok", cached_keys:int}`.

## File inventory

| File | Purpose |
|------|---------|
| `main.py` | Boots consumer thread + reconcile thread + uvicorn (main thread); joins workers on shutdown. |
| `best_laps_cache/settings.py` | Env-driven `Settings` snapshot; validates `LAKE_TABLE`/`LAKE_COL_BEST_TIME` as SQL identifiers. |
| `best_laps_cache/store.py` | `BestLapsStore` — thread-safe mirror; key encode/decode; `update_live` (min), `merge_reconcile` (min), `query`. |
| `best_laps_cache/lakehouse_client.py` | Ported `/query` client: `verify=False`, bounded retry, CSV parse (`read_csv(low_memory=False, dtype=str)` on the five partition columns to suppress pandas `DtypeWarning` from chunked type inference). |
| `best_laps_cache/enrichment.py` | Session cache + DCM resolution; `enrich()` for the hot path. |
| `best_laps_cache/reconcile.py` | `ReconcileWorker` (serialized daemon), `build_reconcile_sql`, `reduce_rows`. |
| `best_laps_cache/consumer.py` | `RawConsumer` — QuixStreams Application + manual `get_consumer()` poll loop (no `app.run()`, no signal handlers) dispatching raw/session/config to the in-memory index + enrichment. |
| `best_laps_cache/api.py` | FastAPI app, `GET /best-laps` (CSV/JSON), `GET /healthz`. |
| `app.yaml` / `dockerfile` / `pyproject.toml` / `.dockerignore` | Quix deployment + container. |
| `tests/` | `test_store.py`, `test_reconcile.py`, `test_api.py`. |
| `quix.yaml` (root) | New `best-laps-cache` deployment block (state mount + `blobStorage: bind: true` for auto-injected lake vars). |

## Integration with neighbouring features

- **Lakehouse / lake sink:** reads `ac_telemetry_prod` (per-environment
  `AC_TELEMETRY_TABLE_NAME` project variable) via the auto-injected
  `Quix__Lakehouse__Query__*` vars (`blobStorage: bind: true`). Read-only.
- **DCM (`dynamic-configuration-manager`):** same `/api/v1/configurations`
  latest-version resolution as `leaderboard-service` and
  `session-config-bridge`.
- **Leaderboard (future phase):** designed so the leaderboard's
  `_query_best_laps_min` lake call can be replaced by a GET to this service's
  `/best-laps?...` with the same columns and per-`(group, driver)` reduction
  already applied. Not wired in Phase 1.

## Caveats

- **No warm start across redeploys.** There is no durable State store; on a
  restart the API serves an empty index until the first reconcile (kicked on
  start) and incoming live ticks repopulate it — one full scan, seconds. If
  redeploy warmth is later required, add it on the `BestLapsStore` side (e.g.
  a periodic snapshot to blob), not by reintroducing a partition-scoped
  RocksDB store that the HTTP/reconcile threads can't safely read.
- The session-topic handler keys the enrichment cache by a `hostname` field
  if the payload carries one, else a constant `"default"` — adequate for the
  single-sim deployment (most-recent-entry resolution), matching the
  leaderboard's assumption.
