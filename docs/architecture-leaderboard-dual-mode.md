# Architecture — Leaderboard Dual-Mode Behaviour (Live vs. Idle)

## What this is

The Analysis → Leaderboard tab now reacts to whether an AC session is
actively broadcasting:

- **Idle (no active stream).** The Left "Live Sector Comparison" table
  shows an empty-state message. The Right "Best Laps" table is driven
  by the cascading Experiment → Track + Car dropdowns the user picks
  manually.
- **Live (active stream).** A new "Follow live driver" toggle appears
  at the top of the filter bar. When ON (default, persisted to
  `localStorage`), the dropdowns are visually disabled and the Right
  table re-fetches against the live `(experiment, track, car)`
  automatically. When OFF, dropdowns re-enable and the Right table
  reflects the user's manual selection. The Left table is always
  driven by the live stream when one exists.

This work also activates the deferred Step-2 gate-vector pipeline (per
`docs/architecture-leaderboard-checkpoint-gates.md`) and fixes two
wire-correctness bugs that were dormant under the previous
polling-only path:

- **Bug A — driver-name case mismatch.** Every WebSocket envelope now
  carries the Mongo display-case driver name (`"Tomás"`), never the
  lake-folded form (`"tomas"`). The frontend's exact-equality match
  against `row.driver` therefore always succeeds.
- **Bug B — gate-state computed per tick.** When the active driver
  crosses a checkpoint gate, the consumer thread immediately
  recomputes `last_gate_index` / `last_gate_state` /
  `last_gate_delta_ms` (and the per-historical inline deltas) using
  the SAME formula the snapshot-rebuild path uses. The previous code
  only updated those fields at snapshot rebuild, so the sticky values
  would go stale for tens of seconds between rebuilds.

## Why this design

- **Explicit `active_state` envelope.** Earlier drafts inferred
  liveness from the presence of `active` mutations + a client-side
  timeout. Pushing an explicit transition envelope ("idle→active",
  "combo change", "active→idle") moves all timeout logic to the
  backend and exposes a clean `isLive` boolean to every consumer.
- **20 s server hysteresis vs. 10 s active-row stale.** Active-row
  mutations stop after `STALE_AFTER_S = 10 s`, but the `active_state`
  toggle visibility uses `2 × STALE_AFTER_S = 20 s` so a brief pause
  in the sim doesn't flicker the toggle button on/off (spec §8).
- **`localStorage` toggle, default `true`.** Users mostly want the
  Right table to follow whoever is driving. Persisting the choice
  across refreshes and sessions matches the spec's locked default.
- **Median rule + 50 ms neutral band.** `last_gate_state` compares the
  active driver's cumulative time at gate `i*` against the **median**
  of every cached historical's `gate_vector[i*]`. Inside a 50 ms
  window around the median the row paints `"neutral"`; outside it,
  `"ahead"` (active faster) or `"behind"` (active slower). The
  median is more stable than the previous `all(< h)` / `all(> h)`
  rule against the leader when historicals are sparse.
- **Single source of truth for gate math.** Both the snapshot-rebuild
  path (`routes/leaderboard_real.py`) and the per-tick path
  (`live_telemetry._record_message`) call into
  `api/gate_math.py:compute_last_gate_state`. The neutral leaf
  module breaks the circular import that would otherwise occur if we
  imported `_compute_last_gate_state` back from `leaderboard_real`
  into `live_telemetry`.
- **Per-historical deltas inline on the active mutation.** The
  Right-table doesn't need them (it's the Best Laps panel), but the
  Left table renders each historical row's `delta_at_last_gate_ms`
  per spec §10. Server-pushed inline (≈80 B per gate crossing × ≤20
  gates per lap) is trivial on the wire and saves the frontend from
  duplicating the gate-state state machine.

## Data flow

```
Kafka ac-telemetry-raw / ac-telemetry-session / ac-telemetry-config
                  │
                  │ (consumer thread, daemon)
                  ▼
        live_telemetry._record_message
        ┌─────────────────────────────────────────┐
        │ 1. Update gate_times_ms (lap rollover-  │
        │    safe).                               │
        │ 2. If a gate just crossed:              │
        │      gate_math.compute_last_gate_state  │
        │      gate_math.compute_per_historical_  │
        │        deltas                           │
        │ 3. Resolve folded driver -> display via │
        │    Mongo lookup cache.                  │
        │ 4. Publish `active` envelope (+         │
        │    historical_deltas inline).           │
        │ 5. Update canonical active_state;       │
        │    publish `active_state` envelope on   │
        │    every transition.                    │
        └─────────────────────────────────────────┘
                  │
                  ▼
        live_stream queue + broadcaster
        ┌─────────────────────────────────────────┐
        │ Cross-thread handoff onto FastAPI loop. │
        │ THROTTLE_MS=50 (≈20 Hz) on active.      │
        │ Full snapshots + active_state +         │
        │   keepalive pings bypass the throttle.  │
        └─────────────────────────────────────────┘
                  │
                  ▼
        WebSocket clients → useLiveStream hook
        ┌─────────────────────────────────────────┐
        │ snapshot   → replace rows               │
        │ active     → patch active row by        │
        │              (driver, track, car, exp), │
        │              + patch each historical    │
        │              row's delta_at_last_gate_ms│
        │              from historical_deltas     │
        │ active_state → set isLive + liveCombo   │
        │ ping       → no-op                      │
        └─────────────────────────────────────────┘
                  │
                  ▼
        LeaderboardTab
        ┌─────────────────────────────────────────┐
        │ effectiveExperiment/Track/Car =         │
        │   isLive && followLive                  │
        │     ? liveCombo                         │
        │     : userDropdownSelection             │
        │ Right table fetches /best-laps with     │
        │   effective triple; debounced via       │
        │   useEffect on the triple.              │
        └─────────────────────────────────────────┘
```

The keepalive task on the FastAPI side also calls
`live_telemetry.sweep_stale_active_state()` every 25 s. That sweep
demotes the canonical `active_state` to `is_active=false` when every
per-key state entry has been silent for more than
`ACTIVE_STATE_STALE_AFTER_S = 20 s` — the consumer thread can't push
that transition itself because, by definition, it has stopped
receiving ticks.

## Wire shape additions

### `active_state` envelope (new)

```jsonc
{
  "type": "active_state",
  "is_active": true,
  "driver": "Tomás",            // Mongo display case, or null when idle
  "track": "ks_nurburgring",   // or null when idle
  "car": "bmw_1m",             // or null when idle
  "experiment": "exp-validate", // or null when idle
  "environment": "dev"          // or null when idle
}
```

Sent: once immediately after the connect-time snapshot, then on every
transition (idle→active, combo change while active, active→idle).

### `active` envelope (extended)

```jsonc
{
  "type": "active",
  "row": {
    "driver": "Tomás",           // Mongo display case (Bug A fix)
    "track": "...",
    "car": "...",
    "experiment": "...",
    "current_lap": 5,
    "current_lap_time_ms": 47312,
    "normalized_position": 0.42,
    "last_gate_index": 8,
    "last_gate_state": "behind",
    "last_gate_delta_ms": 154
  },
  "historical_deltas": {        // new — spec §7.2
    "Ludvík": -82,              // positive => active slower than historical
    "Alice":  +203
  }
}
```

### `LivePositionEntry` (extended)

```ts
interface LivePositionEntry {
  // ... existing fields ...
  last_gate_index?: number | null
  last_gate_state?: "ahead" | "behind" | "neutral" | null
  last_gate_delta_ms?: number | null    // active vs. median historical
  delta_at_last_gate_ms?: number | null // historical rows only:
                                        //   active - this_historical at i*
}
```

## File inventory

### Backend (created)

- `test-manager-backend/api/gate_math.py` — pure-function helpers for
  the gate-state computation. `compute_last_gate_state`,
  `compute_per_historical_deltas`, `latest_crossed_gate`,
  `to_display_name`. Documented as the single source of truth for the
  median + 50 ms neutral-band formula.

### Backend (modified)

- `test-manager-backend/api/live_telemetry.py`
  - Renamed/clarified `_HistoricalEntry` and `_gate_vectors_cache` —
    no longer marked dormant. They are now the canonical historicals
    cache for the Left-table colour cue.
  - `_record_message`: applies the Mongo display-case lookup before
    publish (Bug A) and recomputes the sticky gate-state triple +
    per-historical deltas on every new gate crossing (Bug B). Publishes
    `active_state` transitions via `_update_active_state`.
  - Added module-level driver-name lookup cache + invalidation hook on
    best-laps refresh.
  - Added `_active_state` + `current_active_state_envelope` +
    `sweep_stale_active_state` for the active-state envelope.
  - `refresh_best_laps_cache` now ALSO calls
    `refresh_gate_vectors_cache` so both caches stay in sync. Also
    invalidates the driver-name lookup.
  - Round-1 fix (nitpicker blocker #1 / "only ludvik visible"):
    `refresh_best_laps_cache` discovers drivers via
    `_query_best_laps_with_lap` (iCurrentTime + ≥0.95 normPos
    coverage) instead of the prior `_query_best_laps` (`iBestTime > 0`
    filter). The dev lake's older replays didn't carry populated
    `iBestTime` per tick, silently dropping every historical except
    the currently-running driver. Coverage-based discovery surfaces
    every driver with a real completed lap regardless of whether AC
    happened to be writing iBestTime at the time.
  - `refresh_gate_vectors_cache` is wired in — uses the new
    `_query_best_laps_with_lap` to discover `(driver, lap)` per
    group, then `_query_gate_samples` + `_reduce_to_gate_vectors`.
  - Removed `set_last_gate_state` (unused — gate state is now
    computed at the source in `_record_message`).

- `test-manager-backend/api/live_stream.py`
  - Added `publish_active_state` + `_broadcast_envelope` for the
    `active_state` transition envelope.
  - Extended `_build_wire_payload` to carry `historical_deltas` on
    the `active` envelope.
  - `_keepalive_loop` also calls `sweep_stale_active_state` so the
    toggle visibility flips off after AC stops (the consumer thread
    can't push that transition on its own).
  - Added LOCAL_DEV_MODE `_sim_driver_loop` so the dev page sees
    periodic active mutations + an initial active-state envelope.

- `test-manager-backend/api/routes/leaderboard_real.py`
  - Removed every `# TODO Step 2` marker.
  - Added `_build_best_laps_with_lap_sql` /
    `_query_best_laps_with_lap` (per-driver best with `(lap)` for the
    gate-samples WHERE clause).
  - `compute_last_gate_state` is now a thin wrapper around
    `gate_math.compute_last_gate_state`. New
    `_compute_per_historical_deltas` wrapper for the row factory.
  - `_build_group_rows` populates `last_gate_*` on the active row and
    `delta_at_last_gate_ms` per historical row.
  - `_solo_active_group` populates the same fields with the
    cold-cache rule (`last_gate_state = "neutral"` once gate 1 is
    crossed).
  - `build_live_positions` reads `live_telemetry.get_gate_vectors_cache()`
    and threads it through `_build_group_rows`.

- `test-manager-backend/api/routes/leaderboard_stream.py`
  - Sends `live_telemetry.current_active_state_envelope()` right after
    the initial snapshot so reconnecting clients learn the current
    liveness without waiting for the next transition.

- `test-manager-backend/api/routes/live_positions_sim.py`
  - `_compute_last_gate_state_sim` now delegates to
    `gate_math.compute_last_gate_state` — sim and real cannot drift
    apart.
  - Each group emits per-historical `delta_at_last_gate_ms` so the
    LOCAL_DEV_MODE colour cycling is visible.

- `test-manager-backend/api/models.py`
  - `LivePositionEntry` gains `delta_at_last_gate_ms: int | None`.

### Frontend (modified)

- `test-manager-frontend/types/leaderboard.ts`
  - `LivePositionEntry` gains `delta_at_last_gate_ms`.
  - Updated docstrings on `last_gate_*` to describe the median +
    50 ms neutral-band rule.

- `test-manager-frontend/lib/hooks/use-live-stream.ts`
  - Added `isLive`, `liveCombo` to `UseLiveStreamResult`.
  - Parses the new `active_state` envelope.
  - `patchActiveRow` also applies per-historical `historical_deltas`
    to every matching historical row in the same group.

- `test-manager-frontend/components/analysis/leaderboard-tab.tsx`
  - Added `FollowLiveToggle` rendered only when `isLive=true`.
  - `localStorage` key `leaderboard.followLive` (default `"true"`).
  - `effectiveExperiment/Track/Car` derived from
    `isLive && followLive ? liveCombo : userDropdownSelection`.
  - Empty-state copy on the Right table updated for the dual-mode
    matrix.

- `test-manager-frontend/components/analysis/live-positions-table.tsx`
  - Empty state when `isLive=false`: "No live session — start an AC
    session to see live sector deltas."
  - Active-row colour cue continues to read from `last_gate_state`.
  - Non-active rows now render the signed
    `delta_at_last_gate_ms` per spec §10 (`+0.123` / `-0.456`,
    rose / emerald / neutral by sign).

## Caching model

Two in-memory caches, refreshed on the same triggers (consumer
startup, `ac-telemetry-session` Kafka event, `ac-telemetry-config`
DCM event for `session` OR `experiment` type):

- `_best_laps_cache` — `{(track, car, exp, env): {folded_driver:
  best_lap_ms}}`. Drives the Right "Best Laps" table.
- `_gate_vectors_cache` — `{(track, car, exp): {folded_driver:
  _HistoricalEntry}}`. Drives the Left "Live Sector Comparison"
  colour cue + per-historical deltas. Note: keyed without `environment`
  because the active-row code path only knows `(track, car,
  experiment)` and every active driver lives in exactly one DCM
  experiment config.

The driver-name display-case lookup is a separate cache rebuilt
lazily on the next publish after every best-laps refresh.

## Failure modes & guards

- **Cold cache.** `_gate_vectors_cache is None` → all gate-state
  fields stay `None` (or `"neutral"` on the active row once gate 1
  is crossed). No exception, no colour paint.
- **Driver-name lookup miss.** Falls back to title-cased folded key
  (`"tomas"` → `"Tomas"`) and logs a WARNING once per unique miss so
  noisy AC sessions don't spam the log.
- **DCM / Mongo down.** Consumer thread keeps recording state with
  empty enrichment; refreshes log and swallow exceptions; the
  previous cache stays valid. The active-state envelope still flips
  to `is_active=true` because the consumer is still receiving raw
  ticks.
- **WS scheduler race.** All cross-thread publishes use
  `asyncio.run_coroutine_threadsafe` and swallow `RuntimeError` on
  loop shutdown.
- **LOCAL_DEV_MODE.** Kafka consumer never starts; the
  `_sim_driver_loop` on the FastAPI side emits 4 Hz active
  mutations + one `active_state` envelope on first frame.

## Integration with neighbouring features

- Builds on `architecture-leaderboard-live-stream.md` (the WebSocket
  contract) — extends the wire shape with `active_state` and adds
  `historical_deltas` to the `active` envelope.
- Activates the gate-vectors pipeline defined in
  `architecture-leaderboard-checkpoint-gates.md` — what that doc
  described as "deferred Step-2 markers" is now live.
- Continues to coexist with the dropdown-driven Right-table fetch
  routes in `architecture-leaderboard-dropdowns.md`. No route schema
  changes; only the frontend's choice of which `(experiment, track,
  car)` to pass.
