# Architecture: Leaderboard lake resilience (non-blocking + retried + crash-proof)

## What this does

Makes `leaderboard-service/api/` resilient to a flaky or slow lakehouse. Three
guarantees:

1. **The live active-driver stream keeps flowing regardless of lake state.**
   Raw-tick consumption (`_record_message` → `live_stream.publish_snapshot`)
   and live-session liveness (`current_live_session`) never block on a lake
   query, even when every lake call is timing out.
2. **Transient lake failures are retried with bounded backoff** in
   `LakehouseClient.query` and the catalog/partition metadata GETs, then give
   up and return stale/empty data — never an exception that crashes a thread.
3. **Every lake call site degrades gracefully** (stale-on-error / empty) and
   logs handled transient timeouts at WARNING (one line, no per-tick
   traceback spam).

This complements `architecture-leaderboard-live-first.md`, which removed the
lake from the **WebSocket connect** path. This change removes it from the
**Kafka consumer poll loop** path — the second place a slow lake could stall
the live stream.

## Why this architecture

### The bug

The live path is raw/Kafka-driven and lake-independent *in principle*. But the
lake-backed historical work — best-laps refresh, gate-vector rebuild, and
partition enumeration — ran **synchronously on the Kafka consumer thread**:

- `_consumer_loop` called `_maybe_refresh_on_ttl()` on **every**
  `consumer.poll(timeout=0.5)` iteration; that did a `_known_groups()` →
  `enumerate_groups()` lake read and, when due, a seconds-long
  `_query_best_laps_min` per group.
- `_handle_session_message`, `_handle_config_event`, and `_record_message`
  (on lap completion) each called `_refresh_best_laps_from_settings(force=True)`
  inline.
- `_adopt_live_session` broadcast its `live_session` envelope via
  `_build_live_session_envelope` → `_resolve_session_experiment` →
  `enumerate_groups()` (a lake round-trip), on the consumer thread.
- The hot per-tick `_handle_raw_message` resolved experiment via the same
  `enumerate_groups()` call.

A 30 s `httpx.ReadTimeout` in any of those blocked the poll loop, so raw ticks
weren't processed and the live stream stalled for the duration. Observed:
repeated 30 s `ReadTimeout`s in `_run_one` → `_query_best_laps_min`.

### The fix: a dedicated lake-work executor

A single daemon worker thread (`ghost-lap-lake-worker`) drains a small bounded
job queue. The consumer thread **submits** lake jobs (non-blocking
`queue.put_nowait`) and immediately returns to `consumer.poll()`. The worker
runs the lake work one job at a time, off the hot path.

- **Why a dedicated thread, not a `ThreadPoolExecutor`:** best-laps and
  gate-vector refreshes already serialise on `_best_laps_refresh_lock`;
  running them one-at-a-time off the consumer thread is exactly the desired
  behaviour, and a single worker keeps the model trivial to reason about.
- **Why a bounded queue (maxsize=4) that drops on full:** every job is an
  idempotent refresh. When the worker is busy (e.g. a query is timing out),
  new submissions are **coalesced away** rather than buffered. The TTL tick
  re-submits every 0.5 s, so nothing is permanently lost and a slow lake can
  never back-pressure raw consumption.

### Hot-path partition resolution

`_resolve_session_experiment` gained a `blocking` flag:

- `blocking=True` (default, off-hot-path: WS connect via `asyncio.to_thread`,
  `resolve_live_session` job) uses `enumerate_groups()` (may hit the lake).
- `blocking=False` (the per-raw-tick hot path) uses the new
  `partition_index.cached_groups()` — a **lake-free** read of the TTL cache.
  A cold cache returns `None` → "no candidates yet"; the tick falls through
  and is retried next tick once the background `warm_partitions` job has
  populated the cache.

`_adopt_live_session` (always on the consumer thread) now broadcasts the
**lake-free** `_build_live_session_envelope_fast` immediately, then submits a
`resolve_live_session` job so the fully lake-aligned envelope is rebroadcast
off-thread (deduped on the wire).

### Retry + backoff

`LakehouseClient._post_with_retry` retries transient failures
(`httpx.TimeoutException`, other `httpx.TransportError`, HTTP 5xx) with fixed
backoffs `(0.5 s, 1.0 s)` → 3 attempts total. Non-retryable errors (4xx,
`LakehouseQueryError`) raise immediately. Worst-case wall time per `query()` is
bounded (~3 × 30 s timeout + 1.5 s backoff ≈ 91.5 s) and only incurred when the
lake is hard-down — always on the worker thread, never the consumer/event loop.
The catalog `/manifest` and `/partitions` metadata GETs reuse the same policy
via `partition_index._get_with_retry` (10 s timeout, same backoffs).

### Log noise

`_log_lake_call_failure` logs a **handled transient** lake failure (timeout /
transport / 5xx, retries already exhausted) as a single WARNING line with no
traceback — the lake being slow is operational, not a code bug, and per-tick
ERROR+stacktrace floods the logs. A genuinely unexpected error keeps the full
ERROR+traceback. `LakehouseClient.query` / `_get_with_retry` also emit one
WARNING per retried attempt.

## Data flow

```
Kafka consumer thread (poll loop — NEVER blocks on lake)
  consumer.poll(0.5)
   ├─ _maybe_refresh_on_ttl()        → submit_lake_job("best_laps_ttl")
   │                                    submit_lake_job("warm_partitions")   [non-blocking]
   ├─ _handle_session_message()      → submit_lake_job("best_laps_force")
   ├─ _handle_config_event()         → submit_lake_job("best_laps_force")
   ├─ _handle_raw_message()          → _record_message()  ── live path, lake-free
   │     │                               └─ publish_snapshot()  → WS active row
   │     └─ _resolve_session_experiment(blocking=False) → partition_index.cached_groups()  [lake-free]
   └─ _adopt_live_session()          → publish fast (lake-free) envelope
                                        submit_lake_job("resolve_live_session")

ghost-lap-lake-worker thread (drains queue; lake blocking is fine here)
   _lake_worker_loop()
     ├─ best_laps_force      → _refresh_best_laps_from_settings(force=True)
     ├─ best_laps_ttl        → _maybe_refresh_due_on_worker()  (due-check + refresh)
     ├─ warm_partitions      → partition_index.enumerate_groups()  (warms TTL cache)
     └─ resolve_live_session → resolve_and_publish_live_session()  (lake-aligned envelope)
            every lake call: bounded retry → stale-on-error (keep prev cache / empty)
```

Result: with the lake fully down, the consumer keeps polling, `_record_message`
keeps publishing live snapshots, and `current_live_session` stays live off the
raw-tick clock. Historical panels keep their last-good (stale) data or render
empty; they self-heal once the lake recovers and a worker job succeeds.

## File inventory

- **`api/lakehouse_client.py`** (modified) — added `_RETRY_BACKOFFS_S`,
  `_is_retryable`, and `LakehouseClient._post_with_retry`; `query` now retries
  transient failures and logs each retry at WARNING.
- **`api/partition_index.py`** (modified) — added `_get_with_retry` (bounded
  retry for the metadata GETs) wired into the manifest + `/partitions`
  fetchers, and `cached_groups()` (non-blocking lake-free cache read for the
  hot path).
- **`api/live_telemetry.py`** (modified) — added the lake-work executor
  (`start_lake_worker` / `stop_lake_worker` / `submit_lake_job` /
  `_run_lake_job` / `_lake_worker_loop`); converted all consumer-thread lake
  refreshes to `submit_lake_job`; added `_maybe_refresh_due_on_worker` (the
  worker-side due-check), the `_resolve_session_experiment(blocking=…)` flag,
  the lake-free `_adopt_live_session` broadcast, and `_log_lake_call_failure`
  (WARNING-vs-ERROR classification). `start()` / `stop()` manage the worker
  lifecycle alongside the consumer.
- **`tests/test_lake_resilience.py`** (new) — retry-then-succeed,
  retry-exhausted-then-raise, non-retryable-not-retried, refresh-doesn't-raise
  when the lake always times out, and live snapshot still publishes +
  `current_live_session` still live with the lake down.

## Integration with neighbouring features

- **live-first** (`architecture-leaderboard-live-first.md`): that doc keeps the
  lake off the WS **connect** path; this keeps it off the Kafka **consumer**
  path. Together the live view neither stalls on connect nor stalls mid-session
  when the lake degrades.
- **best-laps + gate-vectors**: the refresh entry points
  (`refresh_best_laps_cache`, `refresh_gate_vectors_cache`) are unchanged in
  shape and still stale-on-error per group; they now simply run on the worker
  thread instead of inline. Result shapes and the WS `snapshot` / `active`
  envelopes are untouched.
- **partition-index**: `enumerate_groups()` semantics (TTL cache,
  stale-on-error, never raises) are unchanged; `cached_groups()` is a new
  read-only accessor and the metadata GETs gained bounded retry.
