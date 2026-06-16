# Architecture — Leaderboard Live-Stream WebSocket

## What this is

A WebSocket channel (`/api/v1/leaderboard/live-stream`) that is the **sole
data source** for the leaderboard tab. The server pushes two kinds of
JSON messages through the same socket:

- `{"type": "snapshot", "rows": [LivePositionEntry, ...]}` — a full
  rebuild of the leaderboard. Sent once on connect and again whenever
  the gate-vectors cache refreshes server-side (i.e. historicals
  changed).
- `{"type": "active", "row": {...}}` — a per-tick mutation of the
  active driver's row, throttled to ~20 Hz.

The frontend keeps a single `rows: LivePositionEntry[]` state. A
`snapshot` replaces it; an `active` message patches the matching row
by `(driver, track, car, experiment)`.

The HTTP polling endpoint `/api/v1/leaderboard/live-positions`
**still exists** as a curl/debug fallback. The frontend does not call
it any more.

## Why this design

### What the previous approach got wrong

The 8 s poll + 50 ms WebSocket per-tick mutation:

- **Double source of truth.** The frontend merged the polled rows
  (full payload) with the WS mutation (active row only). Any drift
  between the two caused brief visual artifacts. Now there's exactly
  one path: the WebSocket.
- **8 s of latency on historicals.** When the gate-vectors cache
  refreshed server-side, the frontend kept showing stale rows for up
  to 8 s. Server-side broadcast of a fresh snapshot eliminates this
  window.
- **Two reconnect strategies.** The poll loop and the WS reconnect
  ran independently. Now there's one reconnect to worry about.

### Why a tagged envelope

The polling endpoint and the WS now carry the same data, but the
per-tick fast-path needs to be small (one row, no historicals) while
the snapshot path needs to be a full list. A tagged envelope lets one
socket serve both with a single parser on the client. The cost — one
extra string field per message — is negligible.

### Why broadcast snapshots on gate-vectors refresh, not on every tick

The gate-vectors cache only mutates when:

- An AC session message arrives (new lap data possible).
- A DCM config event fires for a session-type target (driver swap).
- The consumer warm-up runs at startup.
- The route-layer cold-cache fallback fires (one-off; effectively
  only on the very first request after backend boot).

Each of those is a rare event (seconds-to-minutes apart) so the cost
of broadcasting a fresh snapshot to every client is bounded. The
per-tick fast path stays unchanged: throttled active-only mutations.

### Why build the snapshot on a worker thread for connect

`leaderboard_real.build_live_positions(mongo)` is synchronous — it
runs Mongo queries and may run a QuixLake query on cold cache. Running
it directly on the FastAPI event loop would stall every other
WebSocket connect / HTTP request for the duration. The route handler
wraps the call in `asyncio.to_thread()` so the event loop stays
responsive.

The gate-vectors-refresh broadcast path is already on a worker
thread (the Kafka consumer thread), so it runs the build inline and
hands the resulting `rows` to `live_stream.publish_full_snapshot`,
which does the cross-thread handoff onto the event loop.

### Why a separate module for the broadcaster

`api/live_telemetry.py` lives on the Kafka consumer thread — pure
sync, threading primitives, RLock. The WebSocket broadcaster lives on
the FastAPI event loop — pure async, `asyncio.Lock`, `asyncio.Queue`.
Folding both into one module would force every reader of either to
reason about the other's concurrency model. Keeping them split means:

- `live_telemetry` calls thread-safe helpers (`publish_snapshot`,
  `publish_full_snapshot`) and goes back to its consumer loop.
- `live_stream` only ever touches its own module-level async state.

Both bridges use `asyncio.run_coroutine_threadsafe` against the
captured event loop — the same pattern `telemetry-dashboard/main.py`
uses.

## Data flow

### Connect

```
Browser opens WS ?token=...
  └─► leaderboard_stream.live_stream_endpoint
        ├─► _validate_ws_token  (skipped in LOCAL_DEV_MODE)
        ├─► websocket.accept()
        ├─► asyncio.to_thread(_build_initial_rows_sync, mongo)
        │     └─► leaderboard_real.build_live_positions(mongo)
        │           (or live_positions_sim in LOCAL_DEV_MODE)
        ├─► send_json({"type": "snapshot", "rows": [...]})
        ├─► live_stream.register(websocket)
        └─► await receive_text() loop (keepalive drain)
```

### Per-tick (active mutation)

```
AC shared memory (60 Hz)
  └─► ac-telemetry-source ─► ac-telemetry-raw (Kafka)
        └─► test-manager-backend Kafka thread
              └─► live_telemetry._record_message
                    ├─► updates _state (under RLock)
                    └─► live_stream.publish_snapshot(snapshot)
                          └─► run_coroutine_threadsafe(_enqueue_snapshot, loop)

                                (FastAPI event loop)
                                └─► _queue (maxsize=1, latest-wins)
                                      └─► _broadcaster_loop
                                            ├─► JSON-serialise {"type":"active","row":{...}}
                                            ├─► fan out to every client
                                            │     (drop dead sockets)
                                            └─► sleep THROTTLE_MS (50 ms)
                                                  └─► loop
```

### Full snapshot (historicals changed)

```
AC session change / DCM event / consumer warmup
  └─► live_telemetry._handle_session_message  (or _handle_config_event etc.)
        └─► _refresh_gate_vectors_from_settings
              └─► refresh_gate_vectors_cache
                    ├─► query QuixLake, reduce, swap cache
                    └─► _broadcast_full_snapshot_safely
                          ├─► mongo = api.mongo.get_mongo()
                          ├─► rows = build_live_positions(mongo)
                          │     (synchronous on the Kafka thread —
                          │      we're already off the event loop)
                          └─► live_stream.publish_full_snapshot(rows)
                                └─► run_coroutine_threadsafe(_broadcast_full_snapshot, loop)

                                      (FastAPI event loop)
                                      └─► _broadcast_full_snapshot
                                            ├─► JSON-serialise {"type":"snapshot","rows":[...]}
                                            └─► fan out to every client (no throttle)
```

### Latest-wins queue semantics (active-mutation path)

`_queue` is `asyncio.Queue(maxsize=1)`. When the consumer thread
publishes faster than the broadcaster can drain (always, since the
consumer is at 60 Hz and the broadcaster at 20 Hz), the queue is
already full. `_enqueue_snapshot` drops the previous snapshot before
inserting the new one. The broadcaster always sees the freshest
available value when it wakes — never a stale 50 ms-old one.

Full snapshots bypass this queue entirely: they go straight to
`_broadcast_full_snapshot`, which fans out immediately. A snapshot
arriving in the middle of an active-mutation burst is sent on its own
event-loop tick and the next active mutation continues normally.

### Throttling is post-send, not pre-send

`_broadcaster_loop` does `send → sleep`, not `sleep → send`. That means
the first message of a burst goes out with no added latency; only
subsequent messages within the 50 ms window are coalesced. A cold
client connection that misses by 49 ms still gets the first tick
immediately.

## File inventory

### Backend — `test-manager-backend/`

| File | Change | Purpose |
|------|--------|---------|
| `api/live_stream.py` | modified | Tagged-envelope wire schema (`type=active` / `type=snapshot`). New `publish_full_snapshot(rows)` + `_broadcast_full_snapshot` async helper for un-throttled snapshot fan-out. `_build_wire_payload` now wraps the per-tick fields in `{"type":"active","row":{...}}`. |
| `api/live_telemetry.py` | modified | After every successful `refresh_gate_vectors_cache` swap, calls `_broadcast_full_snapshot_safely`, which resolves the Mongo handle via `api.mongo.get_mongo()`, calls `build_live_positions(mongo)` on the Kafka consumer thread, and hands the rows to `live_stream.publish_full_snapshot`. All exceptions in the broadcast path are swallowed so a Mongo hiccup can't kill the consumer. |
| `api/routes/leaderboard_stream.py` | modified | WS endpoint now takes `mongo: Database = Depends(get_mongo)`. On connect, builds the initial snapshot via `asyncio.to_thread(_build_initial_rows_sync, mongo)` (sim or real path mirrors `routes/leaderboard.py`), sends one `{"type":"snapshot","rows":[...]}` BEFORE registering the client, then drains keepalive frames as before. |
| `api/routes/leaderboard.py` | unchanged | HTTP polling endpoint remains as a curl/debug fallback. |

### Frontend — `test-manager-frontend/`

| File | Change | Purpose |
|------|--------|---------|
| `lib/hooks/use-live-positions.ts` | **deleted** | Polling hook removed entirely. |
| `lib/hooks/use-live-stream.ts` | rewritten | Returns the same shape `useLivePositions` did (`{ rows, tracks, cars, experiments, loading, error }`). Manages `rows` state directly: `snapshot` replaces, `active` patches by key. `loading` is true until the first snapshot. Reconnect backoff (1/2/4/10 s ceiling) unchanged — a fresh snapshot arrives automatically on reconnect. |
| `components/analysis/leaderboard-tab.tsx` | modified | Calls `useLiveStream()` instead of `useLivePositions()`. Same destructuring, same downstream logic. |
| `components/analysis/live-positions-table.tsx` | modified | Removed: `useLiveStream` import (the table no longer subscribes — `leaderboard-tab.tsx` owns the hook), `mergeActiveWithStream`, `useMemo` over the merge. The component now just sorts the incoming `rows` by `rank` and renders. |
| `lib/api/leaderboard.ts` | unchanged | Stays exported for any curl-style debug callers; the leaderboard tab no longer imports it. |

## WebSocket wire schema

### Snapshot envelope (on connect; on historicals refresh)

```json
{
  "type": "snapshot",
  "rows": [
    {
      "track": "ks_nurburgring",
      "car": "bmw_1m",
      "experiment": "LeaderBoard",
      "driver": "Tomas",
      "best_lap_ms": 91234,
      "best_lap_number": 7,
      "is_active": false,
      "current_lap": null,
      "current_lap_time_ms": 45678,
      "rank": 3,
      "last_gate_index": null,
      "last_gate_state": null,
      "last_gate_delta_ms": null
    }
  ]
}
```

`rows` is the exact same flat list of `LivePositionEntry` dicts that
the HTTP polling endpoint returns. Same field names, same types, same
shape — no Pydantic validation between the assembly code and the
wire, so JSON quirks (`null` vs missing field) match what the polling
client would have seen.

### Active mutation envelope (per Kafka tick, ≤20 Hz)

```json
{
  "type": "active",
  "row": {
    "driver": "Tomas",
    "track": "ks_nurburgring",
    "car": "bmw_1m",
    "experiment": "LeaderBoard",
    "current_lap": 2,
    "current_lap_time_ms": 23456,
    "normalized_position": 0.42,
    "last_gate_index": 8,
    "last_gate_state": "ahead",
    "last_gate_delta_ms": -120,
    "active_at_crossing_ms": 23310
  },
  "historical_deltas": { "Alice": -120 },
  "historical_at_positions_next": { "Alice": 50112 },
  "historical_at_positions_at_crossing": { "Alice": 41980 }
}
```

Strict subset of `LivePositionEntry` plus crossing-only extras. The
frontend matches the inner `row` by `(driver, track, car, experiment)`
and only overwrites the row whose `is_active === true`. Mismatches (a
stale active row in the last snapshot vs. a fresh driver in the
mutation) are tolerated — the patch no-ops and the next snapshot
rebroadcast resyncs.

**`active_at_crossing_ms`** (added 2026-06-03) is the active driver's
cumulative time AT the just-crossed gate (`gate_times_ms[last_gate_index]`).
It is non-null only on `newly_crossed` ticks. The frontend's dual gap
chips (spec §3.5) lock onto it as the stable reference each historical's
`gate_vector[last_gate_index]` is compared against — before this field
existed the chips relied solely on the live `current_lap_time_ms`
captured at the crossing frame, which had no wire-carried fallback and
let the red `+X.XXX` chip vanish whenever the frontend's crossing
snapshot was stale or null (notably right after a lap rollover).

**Crossing-detection condition (frontend, corrected 2026-06-03).** The
`use-live-stream.ts` handler fires a `FreezeEvent` (which both opens the
3 s blue freeze and captures the gap-chip snapshot) when
`newIdx != null && newIdx !== prevIdx`. The earlier condition also
required `prevIdx !== null`, which swallowed the FIRST gate crossing of
every new lap (after rollover `last_gate_index` goes 9 → null → 0). The
first crossing of a lap IS a real crossing; suppressing it left the
previous lap's gate-9 snapshot driving the chips for ~10 s into the new
lap. Lap rollover itself (`last_gate_index` → null) still resets freeze
mode to `live` without firing an event; the very next crossing (gate 0)
now re-engages within one frame.

## Authentication

WebSockets in browsers cannot set arbitrary headers on the handshake.
The client appends the bearer token as a query parameter:

```
ws://host/api/v1/leaderboard/live-stream?token=<bearer>
```

The server's `_validate_ws_token` strips a `Bearer ` prefix if present
and calls `auth().validate_permissions(token, "Workspace", workspace_id, "Read")`
— the same check the polling endpoint's `read_permission` dependency
runs. When `api_auth_active=False` (LOCAL_DEV_MODE), validation is
skipped. Failed handshakes close with `1008 Policy Violation`
**before** `accept()` so the browser sees a clean reject.

Token rotation: the `useLiveStream` hook re-subscribes whenever the
`token` from `useQuixAuth` changes, so a Portal-driven refresh kicks
the WebSocket over automatically.

## Failure modes

| Failure | Behaviour |
|---------|-----------|
| FastAPI event loop not running (test collection, startup race) | `publish_snapshot` / `publish_full_snapshot` check `_loop is None` and silently no-op. Consumer loop continues. |
| Event loop closed (shutdown race) | `run_coroutine_threadsafe` raises `RuntimeError`; caught and swallowed. |
| Initial snapshot build fails (Mongo down, LeaderboardError) | `_build_initial_rows_sync` logs and returns `[]`. Client receives a valid (empty) snapshot rather than a 500 close. |
| Initial snapshot send fails (socket already closed) | Caught, log + close with 1011 INTERNAL_ERROR. Client reconnect kicks in. |
| Client `send_text` raises during broadcast | Client is added to a "dead" list, removed under the lock after the fan-out completes. |
| Kafka consumer crashes | Broadcaster keeps running, just receives no active mutations. Frontend keeps the last snapshot rendered. Reconnects do still receive a fresh snapshot from the route layer (which doesn't depend on the consumer). |
| WebSocket disconnect (client) | Frontend reconnects with 1 → 2 → 4 → 10 s backoff. Server delivers a fresh snapshot on the reconnect. |
| AC pauses (source stops) | Consumer receives no ticks, broadcaster receives no active mutations, frontend clock freezes at last value. Next gate-vectors refresh (or reconnect) delivers a snapshot that drops the active row once `STALE_AFTER_S = 10 s` expires. |
| Mongo slow during snapshot broadcast | `_broadcast_full_snapshot_safely` swallows the error and logs; consumer thread continues. Next refresh tries again. |

## Integration with neighbouring features

- **`/live-positions` polling.** Unchanged on the wire; no longer
  called by the leaderboard frontend. Available for curl / manual
  debugging.
- **Gate-state stickiness.** Unchanged. The consumer's
  `set_last_gate_state` still publishes an active-mutation snapshot
  so a gate crossing updates the colour cue within ~50 ms.
- **DCM events (`ac-telemetry-config`).** Unchanged path through
  `_handle_config_event`. Session-type events refresh the
  gate-vectors cache, which now also broadcasts a snapshot — so a
  driver swap in Test Manager appears on every connected leaderboard
  tab within seconds.
- **Telemetry Explorer.** Not affected — separate data path
  (QuixLake on-demand queries).

## Constants worth knowing

| Constant | Where | Value | Why |
|----------|-------|-------|-----|
| `THROTTLE_MS` | `live_stream.py` | 50 | ~20 Hz wire rate for active mutations. Picked so React render budget stays comfortable and 60 Hz consumer ticks coalesce 3:1. Full snapshots are NOT throttled (rare, latency-sensitive). |
| `RECONNECT_BACKOFF_MS` | `use-live-stream.ts` | `[1000, 2000, 4000, 10000]` | Standard exponential backoff with a 10 s ceiling. The ceiling matches `STALE_AFTER_S` so a reconnect within one stale window is the steady state. |
| `STALE_AFTER_S` | `live_telemetry.py` (existing) | 10.0 | The active-driver detection window. The full snapshot rebuild reflects this — once `STALE_AFTER_S` expires `get_active_driver()` returns `None` and the next snapshot drops the active row. |
