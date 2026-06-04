# Architecture — Leaderboard Checkpoint-Gates Redesign

## What this is

A redesign of the Analysis-tab "Live Sector Comparison" colour and
ghost-comparison model. The lap is now divided into **20 checkpoint
gates** by `normalizedCarPosition` (positions 0.05, 0.10, … 1.00). For
every historical driver in the same `(track, car, experiment)` group we
query QuixLake **once per AC session** for that historical's best lap's
per-gate absolute cumulative times — a 20-element vector — and cache
the result.

When the live driver crosses gate `i`, the backend compares the live
cumulative-lap-time at that crossing against every historical's
`gate_vector[i]` and stamps a three-state colour
(`"ahead" | "behind" | "neutral"`) onto the active row. The colour
stays sticky on the active row until gate `i+1` overrides it.
Equal-time at a gate falls into `"neutral"` naturally (`all(<)` and
`all(>)` are both false).

The previous "10 equal segments + 3 s wall-clock blue freeze" model is
gone. The running clock on the active row now ticks continuously via
client-side extrapolation; the only colour cue is the server's
`last_gate_state`. Non-active rows keep the existing rank-based
emerald/rose colouring.

## Why this design

- **Real per-gate cumulative times beat equal splits.** The old ghost
  was `best_lap_ms * (i+1)/10`. That's not what an actual lap looks
  like — corners aren't evenly spaced and a driver might be fast in
  sector 1 and slow at the end. Pulling the real timestamps from the
  lake gives an honest ghost.
- **Once-per-session query cost.** Best laps for completed sessions
  don't change. Caching per AC session start (plus DCM config events
  and consumer startup) drops the lake load to one refresh per session
  instead of one per poll.
- **Three-state colour, not five.** `ahead | behind | neutral` is
  unambiguous and reuses the same emerald/rose hues the rank-based
  colouring already uses elsewhere in the table.
- **Server-computed stickiness.** The active row's
  `last_gate_index` / `last_gate_state` / `last_gate_delta_ms` are
  persisted on the per-key live-telemetry state entry and re-emitted
  every poll. The frontend is stateless on these fields — no client
  timers to drift across lap rollovers.
- **Continuous client clock.** The previous 3-second freeze made the
  table feel slow on every poll. The wall-clock advance is now
  uninterrupted; the colour cue comes from the gate state, not from
  pausing the clock.

## Data flow

```
QuixLake ac_telemetry
    │
    │  (1) Query A: per-lap best-lap aggregation
    │  (2) Query B: position samples for each best lap (OR-disjunction WHERE)
    ▼
leaderboard_real._reduce_to_per_driver_best
    │           +
leaderboard_real._reduce_to_gate_vectors
    │
    ▼
live_telemetry._gate_vectors_cache
    {(track, car, experiment): {driver_folded: _HistoricalEntry}}
    │
    │  (per poll)
    ▼
GET /api/v1/leaderboard/live-positions
    │
    ▼
leaderboard_real.build_live_positions(mongo)
    │
    │  Reads live driver from live_telemetry._state[key]
    │  (gate_times_ms, last_gate_* triple, etc.)
    │
    │  Per group:
    │    historicals' current_lap_time_ms = ghost_ms_at_position(
    │        gate_vector, active.normalizedCarPosition)
    │    active row's last_gate_* = compute_last_gate_state(
    │        active.gate_times_ms, group_historicals)
    │    when a new crossing happens, live_telemetry.set_last_gate_state
    │    persists the triple so subsequent polls re-emit it unchanged
    │
    ▼
LivePositionEntry[]  (with last_gate_index/state/delta_ms on active row)
    │
    ▼
test-manager-frontend LivePositionsTable
    │
    │  Active row colour: emerald if last_gate_state == "ahead",
    │                     rose    if last_gate_state == "behind",
    │                     none    otherwise.
    │  Active row clock: serverElapsedMs + (performance.now() - localT0),
    │                    continuously.
    │  Non-active rows: rank-vs-active colouring (unchanged).
```

## Cache refresh triggers (spec §5.3)

In priority order:

1. **`ac-telemetry-session` Kafka event** — canonical "once per AC
   session" refresh. Handled in
   `live_telemetry._handle_session_message` via
   `_refresh_gate_vectors_from_settings()`.
2. **`ac-telemetry-config` event (DCM)** — config changes can re-bucket
   historicals into different groups. Same hook fires.
3. **Consumer startup warm-up** — `_consumer_loop` calls
   `_refresh_gate_vectors_from_settings()` once, immediately followed
   by `_prewarm_session_cache_from_dcm()` which also refreshes
   gate-vectors after seeding the session cache.
4. **`/live-positions` cold-start fallback** — if the cache is `None`
   on a poll, `build_live_positions` does one synchronous refresh
   inline. Same pattern the retired `_historicals_cache` used.

## Cache shape

```python
@dataclass(frozen=True)
class _HistoricalEntry:
    best_lap_ms: int          # == gate_vector[19]
    best_lap_number: int
    gate_vector: list[int]    # length 20, monotonically non-decreasing

_gate_vectors_cache: (
    dict[tuple[str, str, str], dict[str, _HistoricalEntry]] | None
) = None
```

The outer key is `(track, car, experiment)`. The inner key is the
**folded driver name** (NFKD + ASCII lowercase) so a Mongo `"Ludvík"`
and a lake `"ludvik"` collide on the same lookup.

This cache fully **subsumes the retired `_historicals_cache`**:
`best_lap_ms` and `best_lap_number` are still present on
`_HistoricalEntry`, so any caller that previously needed them reads
them off the entry. The migration is in lockstep with the call sites in
`leaderboard_real.py::_build_group_rows` — there is no half-migrated
state with both caches coexisting.

## Lake query design (spec §5.2)

Two queries per session refresh:

- **Query A** — `_BEST_LAPS_SQL` (unchanged from the prior
  historicals path): per-lap aggregation grouped by
  `(track, carModel, experiment, driver, session_id, lap)`, with
  `lap_time_ms = MAX(timestamp_ms) - MIN(timestamp_ms)`. Reduced in
  Python to the per-driver best lap.

- **Query B** — `_build_gate_samples_sql` (new): position samples for
  the specific `(driver, session_id, lap)` best laps. QuixLake's exact
  SQL dialect for tuple-`IN` is undocumented, so we **commit to a
  flat OR-of-AND disjunction**:

  ```sql
  SELECT track, carModel, experiment, driver, session_id, lap,
         normalizedCarPosition, timestamp_ms
  FROM ac_telemetry
  WHERE (track=? AND carModel=? AND experiment=? AND driver=?
         AND session_id=? AND lap=?)
     OR (track=? AND ... )
     OR ...
    AND normalizedCarPosition IS NOT NULL
  ORDER BY track, carModel, experiment, driver, session_id, lap, timestamp_ms
  ```

  At a cap of 99 historicals per group this comfortably fits in a
  single statement. Tuple-`IN` was not attempted in production —
  disjunction is portable across DuckDB / Postgres / ClickHouse and we
  preferred determinism to a perf micro-optimisation.

The Python reducer (`_reduce_to_gate_vectors`):
1. Buckets samples by `(track, car, exp, driver, session_id, lap)`.
2. For each best-lap pick, finds the matching bucket and sorts by
   `timestamp_ms`.
3. For each of the 20 gates, picks the sample whose
   `normalizedCarPosition` is nearest the target. Tie-breaks by
   earliest timestamp (handles off-track laps that re-cross a gate).
4. Drops the historical entirely if any gate has no sample within
   `0.025` normalised position of the target.
5. Enforces monotonic non-decreasing as a safety net.

## Server-side stickiness and lap rollover

`live_telemetry._record_message` is now responsible for:
- Maintaining `gate_times_ms` (length 20) on every raw tick.
- Detecting lap rollover on **either** `completedLaps` increment **or**
  `iCurrentTime` reset (`i_current < prev_i_current`) — spec §8.7. Both
  conditions clear `gate_times_ms` to `[None]*20`, reset `prev_pos` to
  `0.0`, AND clear the sticky `last_gate_*` triple so the previous
  lap's colour doesn't bleed into the new lap.
- Carrying the sticky `last_gate_*` triple forward unchanged between
  ticks otherwise.

`leaderboard_real._build_group_rows` is responsible for:
- Reading `active.gate_times_ms` and finding the latest crossed gate
  `i*` (max index with a non-None entry).
- If `i*` is the same as the previously-stored `last_gate_index`, the
  state is sticky — re-emit the stored values.
- If `i*` advanced (new crossing), recompute via
  `compute_last_gate_state` and persist via
  `live_telemetry.set_last_gate_state`.
- Historicals' `current_lap_time_ms` comes from
  `ghost_ms_at_position(gate_vector, active.normalizedCarPosition)`
  per spec §5.5.

## LOCAL_DEV_MODE simulator (spec §5.7, §8.5)

`live_positions_sim.py` does not call the lake. Instead it builds a
deterministic `_HistoricalEntry` per historical at module import time
via `_build_sim_gate_vectors_cache()`. Each historical's gate vector
is the equal-split baseline `best_ms * (i+1)/20` perturbed by one of
four profiles (fast-early, slow-early, mid-dip, anti-mid-dip) rotated
by a per-driver phase shift. The result is monotonically
non-decreasing and visibly differs between drivers so the active
row's colour cycles through `ahead → neutral → behind → neutral` as
Ludvík crosses gates.

The sim keeps the existing 3-sector rank function for ranking
purposes (cheap, stable, no behaviour change for the rank column).
Gate-times tracking on the active driver runs **in parallel** to the
sector model — they serve different purposes: sectors drive rank,
gates drive colour state. Spec §8.5 calls out this dual model.

The retired `EQUAL_SPLITS` and `sector_window_from_norm_pos` helpers
are gone; they were only used by the real-mode path's now-replaced
ghost interpolation.

## Frontend changes (spec §5.9)

`test-manager-frontend/components/analysis/live-positions-table.tsx`:

- Removed `FREEZE_AFTER_POLL_MS`, the `isFrozen` state, and the freeze
  branch that turned the active row blue for 3 seconds after every
  poll.
- Removed the corresponding `else if (row.is_active && isFrozen)`
  colour branch.
- The active row's "At Position" colour class is now driven by
  `row.last_gate_state`:
  - `"ahead"` → `font-semibold text-emerald-400`
  - `"behind"` → `font-semibold text-rose-400`
  - `"neutral"` / `null` / `undefined` → no class (default text).
- Non-active rows keep the rank-vs-active emerald/rose colouring.
- The active row's left-border + bg blue tint (`border-l-blue-500
  bg-blue-500/10`) stays — that's the "this is the live driver"
  identity marker, separate from gate-state colouring.
- The running-clock extrapolation (`anchorRef` + `performance.now()`)
  works unchanged; the wall-clock advance was always correct, only the
  freeze-driven colour swap goes away.
- Header subtitle changed from "Re-ranks at sector boundaries" to
  "Re-ranks at checkpoint gates".

`test-manager-frontend/types/leaderboard.ts` mirrors the Pydantic
model with three new optional fields: `last_gate_index`,
`last_gate_state`, `last_gate_delta_ms`. They are all optional on the
TS side so old backends (which don't ship them) still type-check.

## File inventory

**Modified:**

- `test-manager-backend/api/models.py` — `LivePositionEntry` gets
  three new optional fields (`last_gate_index`, `last_gate_state`,
  `last_gate_delta_ms`); `LiveDriverState.segment_times_ms` renamed to
  `gate_times_ms` (length 20).
- `test-manager-backend/api/live_telemetry.py` — `SEGMENT_COUNT` →
  `GATE_COUNT = 20`; `_update_segment_times` → `_update_gate_times`;
  `_historicals_cache` retired and replaced by `_gate_vectors_cache`
  with `_HistoricalEntry` dataclass; new `refresh_gate_vectors_cache`
  + `_refresh_gate_vectors_from_settings` (replacing the historicals
  pair); `_record_message` extended with sticky `last_gate_*` fields
  and lap-rollover clearing per §8.7; new `set_last_gate_state`
  helper; dead `simulated_ghost_reference` and
  `segment_cumulative_from_samples` removed (no callers).
- `test-manager-backend/api/routes/leaderboard_real.py` — new
  `_build_gate_samples_sql` (disjunction form), `_query_gate_samples`,
  `_reduce_to_gate_vectors`, `ghost_ms_at_position`,
  `_latest_crossed_gate`, `compute_last_gate_state`;
  `_historicals_for_group` rewritten to consume `_HistoricalEntry`;
  `_build_group_rows` rewritten to consume the new cache shape and
  compute / persist the sticky `last_gate_*` triple on the active row;
  `EQUAL_SPLITS` import removed.
- `test-manager-backend/api/routes/live_positions_sim.py` — new
  deterministic gate-vector generator (`_GATE_PERTURBATIONS`,
  `_sim_gate_vector`, `_build_sim_gate_vectors_cache`,
  `_sim_gate_vectors_cache`); active-driver state extended with
  `gate_times_ms`, `last_norm_pos`, sticky `last_gate_*` fields;
  `_build_group` computes the gate-state in parallel to the existing
  sector-based rank; `EQUAL_SPLITS` and `sector_window_from_norm_pos`
  removed.
- `test-manager-frontend/types/leaderboard.ts` — three new optional
  fields on `LivePositionEntry`.
- `test-manager-frontend/components/analysis/live-positions-table.tsx`
  — `FREEZE_AFTER_POLL_MS` and the `isFrozen` state machine removed;
  active row's "At Position" colour class driven by
  `row.last_gate_state`; running clock extrapolation is now continuous;
  header subtitle updated.

**Not touched** (per spec scope): `ac-telemetry-source`,
`ac-telemetry-lake`, `session-config-bridge`, DCM, any other service.

## Integration with neighbouring features

- **Best Laps table** — shares the `/live-positions` payload. Reads
  `best_lap_ms` + `best_lap_number` (unchanged shape) and the new
  fields are ignored.
- **DCM event flow** (`ac-telemetry-config` Kafka topic) — same hook
  point as before; the renamed `_refresh_gate_vectors_from_settings`
  swaps in cleanly.
- **`ac-telemetry-session` Kafka topic** — same handler
  (`_handle_session_message`), same one-call-per-session refresh
  cadence.
- **Telemetry Explorer / Analysis tabs** — no changes; they read
  different endpoints.

## Open items / future work

- **§8.4 server-side `iCurrentTime` extrapolation.** The spec defers
  this until a field test confirms AC stalls `iCurrentTime` when the
  car is stationary. The client-side wall-clock extrapolation already
  forward-runs the displayed number; if real-mode shows a lag during a
  long stop, add `last_seen_epoch`-based extrapolation in
  `_record_message` / `get_active_driver`.
- **Tuple-`IN` short-circuit.** If a future QuixLake upgrade documents
  tuple-`IN` support and benchmarks favour it, swap the disjunction in
  `_build_gate_samples_sql` for a tuple-`IN` literal. Behaviour is
  identical; only the wire-bytes change.
