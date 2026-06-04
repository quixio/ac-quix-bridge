# Architecture — Multi-driver Live Positions Leaderboard

## What this is

The Analysis-tab "Leaderboard" is a multi-driver live-positions view.
For a chosen (track, car, experiment) combination it shows **two
stacked tables** fed by the same `/live-positions` payload:

1. **Live Sector Comparison** — Rank · Driver · Best Lap · At Position.
   Re-ranks at sector boundaries by who is fastest at the active driver's
   current map position. Coloring on the At Position column is driven by
   **rank vs. the active driver**: rows ranked better are emerald, rows
   ranked worse are rose. The active driver's At Position cell ticks
   client-side at 200 ms between polls so the running clock advances
   visually instead of jumping every 3.5 s.
2. **Best Laps** — Rank · Driver · Best Lap. Same payload, sorted
   client-side by `best_lap_ms` ascending, ranked 1..N fresh.
   Updates only when a new personal best is set; coloring is neutral.

This document supersedes the prior ghost-lap-vs-self design. The
`/live-driver` and `/ghost-reference` endpoints, the `LiveDriverState` /
`GhostReference` frontend types, the ghost-lap hook and the
segment-breakdown table component have all been removed.

## Why this design

- **One screen, many drivers.** Ludvik's goal is a leaderboard, not a
  delta-to-self timer. A 5-row table reads at a glance.
- **Rank shifts at sector boundaries.** Comparing only at sector ends
  (rather than 60 Hz interpolation) mirrors how real-world live timing
  works and makes the rank changes visually distinct — they only happen
  three times per lap.
- **Server-computed rank.** The backend ranks; the frontend just sorts
  by `rank`. Keeps the UI stateless and matches the eventual real-mode
  shape where lake-driven comparisons need server-side data anyway.
- **Ghost-interpolated "At Position" for everyone.** Each historical
  driver's "At Position" cell is *their estimated time at the active
  driver's current map point*, so the gaps stay comparable as the
  active driver moves around the lap.
- **Color by rank, not by time.** The At Position cells are coloured by
  the row's rank relative to the active driver's rank — green for rows
  above the active, red for rows below — so the cue stays stable across
  the lap even when the absolute "time at this position" numbers
  fluctuate. Coloring by raw time was confusing because every historical
  is always either earlier or later than the active depending on which
  sector they're in.
- **Realtime clock for the active driver.** Polling stays at 3.5 s; a
  200 ms `setInterval` extrapolates the active row's `current_lap_time_ms`
  using `(performance.now() - localT0)` between polls. The next poll
  re-anchors `(serverElapsedMs, localT0)`; if the new server value is
  smaller (lap rolled over) we snap immediately. Historicals don't tick
  — they're ghost estimates re-anchored to the active driver's poll-time
  position, so client-side interpolation would be meaningless.
- **Best Laps split into its own table.** Sorting the live table by best
  lap would mask the sector-by-sector rank dance; keeping the two views
  separate gives Ludvík both "who's actually fastest overall" (Best Laps)
  and "where do I sit right now" (Live Sector Comparison) at a glance.
- **Real mode lives in `leaderboard_real.py`.** LOCAL_DEV_MODE stays
  byte-identical (same simulator module, never imported by real mode).
  Real mode queries QuixLake for historical bests, reads the live
  driver from the existing `live_telemetry` consumer, and reuses the
  simulator's ghost-interpolation helpers via the public
  `ghost_time_for_splits` / `rank_group` / `sector_window_from_norm_pos`
  surface.

## Data flow

```
┌──────────────────────────┐  poll 3500 ms  ┌──────────────────────────┐
│ Browser                  │ ─────────────► │ test-manager-backend     │
│ leaderboard-tab.tsx      │                │ /leaderboard/            │
│ use-live-positions.ts    │ ◄───────────── │   live-positions         │
└────────────┬─────────────┘  LivePosition  └────────────┬─────────────┘
             │ Entry[60]                                 │
             │                                           ▼
             │ filter by                          ┌────────────────────┐
             │  (track, car, experiment)          │ LOCAL_DEV_MODE     │
             ▼                                    │  live_positions_   │
   ┌────────────────────────┐                     │    sim.py          │
   │ LivePositionsTable     │                     │    (60 rows)       │
   │  5 rows sorted by rank │                     └────────────────────┘
   └────────────────────────┘
```

Real-mode flow (when `LOCAL_DEV_MODE != "true"`):

```
                                ┌────────────────────────────┐
                                │ leaderboard_real.py        │
                                │   build_live_positions()   │
   ┌────────────┐ /query  ┌────►│                            │
   │ QuixLake   │◄────────┤     │   ┌──────────────────────┐ │
   │ ac_telemetry│         │     │   │ _reduce_to_per_     │ │
   └────────────┘         │     │   │   _driver_best       │ │
                          │     │   └──────────────────────┘ │
                          │     │           │                │
                          │     │           ▼                │
   ┌────────────┐         │     │   ┌──────────────────────┐ │
   │ Mongo      │  drivers│     │   │ _build_group_rows    │ │
   │  drivers   │◄────────┤     │   │ + sim.ghost_time_for │ │
   └────────────┘         │     │   │   _splits / rank_grp │ │
                          │     │   └──────────────────────┘ │
                          │     │           ▲                │
   ┌────────────┐ in-proc │     │           │                │
   │ live_      │◄────────┤     │   ┌──────────────────────┐ │
   │ telemetry  │  active │     │   │ live_telemetry.get_  │ │
   │ (Kafka)    │  driver │     │   │   _active_driver()   │ │
   └────────────┘         │     │   └──────────────────────┘ │
                                └────────────────────────────┘
```

The QuixStreams consumer thread in `live_telemetry.py` runs whenever
`LIVE_TELEMETRY_ENABLED=true` and `LOCAL_DEV_MODE!=true`. It keeps the
most-recently-seen tick per `(track, car, driver)` and reports stale
(>10 s) entries as no-active-driver via `get_active_driver()`.

### Historicals caching (per-driver best laps)

`build_live_positions` previously queried QuixLake on every HTTP poll
(~every 8 s while a user has the leaderboard tab open). The underlying
per-driver best laps in `ac_telemetry` only change when a new fast lap
completes, so the per-poll lake hit was wasted work.

The cache lives in `live_telemetry.py` (next to the live-driver state it
sits beside) so the route stays a thin assembler. Shape matches
`_reduce_to_per_driver_best`'s return so the existing assembly code is
unchanged:

```python
_historicals_cache: dict[tuple[str, str, str, str], tuple[int, int]] | None
#                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#                       (track, carModel, experiment, driver) → (best_ms, lap)
```

`None` is the "never refreshed yet" sentinel — distinct from "refreshed
but the lake had no rows" (empty dict).

Refresh triggers (in order of importance):

1. **`ac-telemetry-session` arrival.** Every AC session-start produces
   exactly one session message. `_handle_session_message` calls
   `_refresh_historicals_from_settings()` after updating the per-host
   session + experiment caches. This is the primary path; in steady
   state a session-start produces exactly one lake query.
2. **Consumer warm-up.** `_consumer_loop` calls
   `_refresh_historicals_from_settings()` once before entering the
   poll loop. Covers backend restarts mid-session — without this the
   first `/live-positions` after restart would hit the route's
   synchronous fallback.
3. **Route-layer cache miss.** `build_live_positions` checks for `None`
   and triggers a synchronous `refresh_historicals_cache(...)` so a
   cold-start poll arriving before any other trigger still serves
   data. Only one of these fires per backend life because step 2 sets
   the cache to a non-`None` value (possibly empty dict) thereafter.

Concurrency:

- Dedicated `_historicals_lock` (an `RLock`, separate from `_state_lock`).
  Holding `_state_lock` across a seconds-long lake query would stall the
  raw-tick recorder; the dedicated lock keeps the hot path independent.
- The lake query and Python reduction run **outside** the lock. Only the
  final reference swap (`_historicals_cache = reduced`) is guarded. A
  concurrent reader sees either the old dict or the new dict, never an
  in-progress merge — Python's GIL gives us atomic name rebinding for
  free.
- The returned dict is the live reference, not a deep copy. Readers
  must treat it as immutable. Since refreshes only rebind the module
  name and never mutate the existing dict in place, an in-flight reader
  iterating the old reference is safe.

Failure handling: `refresh_historicals_cache` catches every exception
(lake unreachable, malformed rows, etc.) and logs without raising. The
previous cache value stays valid, so the next poll degrades to stale
data rather than 500. The route's cache-miss fallback still re-raises
as `LeaderboardError` if the refresh leaves the cache `None` (cold
start + lake down → caller gets 500).

Circular-import note: `live_telemetry` imports `_query_lake` and
`_reduce_to_per_driver_best` from `routes.leaderboard_real` *lazily*
inside `refresh_historicals_cache`. `leaderboard_real` already imports
`live_telemetry` at module load, so a top-level import in the reverse
direction would cycle. The lazy import is paid once per refresh (rare).

### Raw-tick enrichment (track / car / driver / experiment)

The high-frequency `ac-telemetry-raw` payload only carries
physics/graphics fields; the static identifiers the leaderboard groups
by — `track`, `carModel`, `playerName` (→ `driver`), and `experiment`
— live elsewhere. The lake sink solves this by joining
`ac-telemetry-raw ⨝ ac-telemetry-config`; the in-process leaderboard
consumer can't reuse that, so `live_telemetry.py` keeps its own
per-hostname caches and enriches every raw tick before recording.

```
ac-telemetry-session ──► _handle_session_message ──► _session_cache[host]
       (key=host)                │                    {track,carModel,playerName}
                                 ▼
                          _fetch_experiment_from_dcm(host)
                                 │   (one HTTP call per session change)
                                 ▼
                          _experiment_cache[host] = {experiment, driver, fetched_epoch}

ac-telemetry-config ──► _handle_config_event ──┬─► _session_cache[host]    (type=session)
       (DCM events)                            └─► _experiment_cache[host] (type=experiment)
                                                   (no AC-session-restart needed)

ac-telemetry-raw ────► _handle_raw_message ──► merge from caches ──► _record_message
       (key=host)                                  (track, carModel,
                                                    driver=DCM.driver
                                                          ‖ playerName,
                                                    experiment)
```

Properties of this enrichment path:

- **Single consumer, three subscriptions.** One `quixstreams.Application`
  subscribes to all three topics; the poll loop dispatches by
  `msg.topic()`. Session and config-event messages are rare so
  contention on the shared `_state_lock` is negligible.
- **DCM lookup is session-driven, not per-tick.** The `experiment` and
  `driver` are fetched on every session-message arrival and cached
  forever per hostname (with a 300 s TTL fence as a safety net). The
  hot per-tick path only reads the cache.
- **`ac-telemetry-config` events update caches in real-time.** DCM
  publishes a `{event, contentUrl, metadata}` envelope on every
  configuration create / update / delete. `_handle_config_event`
  validates `metadata.category == "ac-telemetry"` and
  `metadata.type ∈ {session, experiment}`, then either pops the cache
  entry (`event == "deleted"`) or HTTP-GETs the absolute `contentUrl`
  and overwrites the entry. Session-type events additionally
  force-refresh the experiment cache and the historicals cache — same
  hooks `_handle_session_message` already runs. Experiment-type events
  skip the historicals refresh (no laps changed).
- **DCM events are additive, not a replacement for `ac-telemetry-session`.**
  AC's `ac-telemetry-source` writes track / carModel / playerName
  directly to `ac-telemetry-session`, which is the source of truth for
  those three fields. DCM's session config is a copy made by
  `session-config-bridge` and only exists if/after that bridge runs
  successfully — a sim PC that has never been linked to a Test Manager
  test has no DCM session config. Keeping both paths means the
  fast/direct one still works for unlinked hosts.
- **`auto_offset_reset="latest"` on all three topics.** DCM events for
  past changes are uninteresting — the boot-time DCM prewarm already
  reconstructs the same state by walking `/configurations` once.
- **Cache miss = silent drop.** If a raw tick arrives before any session
  message for that hostname (backend started mid-session, or AC source
  restarted), the tick is dropped with a `DEBUG` log. The next session
  change — either AC-driven or DCM-event-driven — will populate the
  cache and traffic resumes. Empty `experiment` is tolerated
  downstream — `_build_group_rows` already handles solo-active-group
  when the historical lake has no matching group, but solo emission
  requires non-empty experiment per `build_live_positions`.
- **DCM unreachable ≠ tick loss.** A DCM failure caches `experiment=""`
  and returns; raw ticks still flow with track/car/driver populated.
  The active driver still appears on the leaderboard for any existing
  `(track, car, experiment)` historical group; only solo-group
  rendering is impacted.
- **Settings reuse.** The DCM URL and SDK token come from existing
  `Settings.config_api_url` / `Settings.sdk_token` — no new env vars.

## Simulator math (LOCAL_DEV_MODE)

Lives in `test-manager-backend/api/routes/live_positions_sim.py`.

### Static matrix (60 rows)

```
TRACKS:      ks_nurburgring, spa, silverstone        (3)
CARS:        bmw_1m, ferrari_488                     (2)
EXPERIMENTS: baseline, tuned                         (2)
DRIVERS:     Ludvík (active), Alice, Bob, Carla, Diego  (5)
```

### Historical best lap

Identical formula for every driver — `Ludvík`'s historical best is
populated only after he completes his first lap in this sim run:

```
base_ms       = 90_000 + track_idx * 4_000 + car_idx * 2_500
exp_offset    = -1_500 if experiment == "tuned" else 0
driver_offset = driver_idx * 420 + (driver_idx ** 2) * 37
best_lap_ms   = base_ms + exp_offset + driver_offset
```

Driver indices: `Ludvík=0`, `Alice=1`, `Bob=2`, `Carla=3`, `Diego=4`.
The quadratic driver term spreads the four historical drivers enough
that ranks visibly shuffle between groups.

### Per-driver sector splits

Each driver's lap is split into three sectors. The fractions are of
*that driver's own best_lap_ms* and each row sums to 1.0:

```
Ludvík:  0.33 / 0.33 / 0.34   (active driver — defines track sectors)
Alice:   0.31 / 0.34 / 0.35   (strong start, weak end)
Bob:     0.36 / 0.32 / 0.32   (slow start, fast middle/end)
Carla:   0.34 / 0.33 / 0.33   (even)
Diego:   0.33 / 0.31 / 0.36   (fast middle, weak finish)
```

### Active driver lap-roll state

Module-level dict keyed by `(track, car, experiment)`:

```python
_LUDVIK_STATE[key] = {
    "lap_start_epoch": float,    # time.time()
    "current_lap": int,          # 1-based
    "best_lap_ms": int | None,   # populated when he first finishes a lap
    "current_lap_ms": int,       # per-lap target, jittered ±400 ms
}
```

Each request runs `_advance_state` which rolls the lap forward in a
`while elapsed_ms >= current_lap_ms` loop, incrementing `current_lap`,
updating `best_lap_ms` when faster, recomputing `current_lap_ms` with
`((current_lap * 137) % 800) - 400` ms jitter, and advancing
`lap_start_epoch` by the lap's target duration (not to `now` — that
would discard the leftover-from-rollover into the next lap).

### Sector and rank evaluation

Per request, after `_advance_state`:

```
t0, t1, t2 = current_lap_ms * 0.33, current_lap_ms * 0.66, current_lap_ms
completed = 0 if elapsed < t0
            1 if t0 <= elapsed < t1
            2 if elapsed >= t1
```

- `completed == 0` (still in sector 0): active driver gets rank 5;
  historical drivers rank 1..4 by `best_lap_ms` ascending.
- `completed >= 1`: every driver's cumulative-at-completed-sector-boundary
  is computed (`sum(lap_ms * splits[:completed])` per driver — using
  `current_lap_ms` for active, `best_lap_ms` for historical). All five
  are sorted ascending → that becomes the rank.

### Ghost interpolation ("At Position" for historical drivers)

The active driver's row shows real elapsed time. Each historical row
shows an *estimate* of where they'd be on their own best lap at the
active driver's current map point:

```
f                = fraction through the active driver's current sector
                   = (elapsed - sector_start_ms) / (sector_end_ms - sector_start_ms)
sector_duration  = historical.best_lap_ms * historical.splits[active_sector]
cum_start        = sum(historical.best_lap_ms * historical.splits[:active_sector])
estimate         = cum_start + sector_duration * f
```

`f` is clamped to `[0, 1]`. When `elapsed` is effectively zero (just
after a lap rollover) every historical row's estimate is forced to 0.

### Why same `now` across the 60 rows

`make_local_dev_live_positions()` captures `time.time()` once and feeds
the same value into all 12 groups. This guarantees the response is a
coherent snapshot (no drift between earlier and later rows in the
list) — important because the frontend filters in-memory after fetch.

## Endpoint

`GET /api/v1/leaderboard/live-positions` →
`list[LivePositionEntry]`.

- `LOCAL_DEV_MODE=true` → 60 rows (3 × 2 × 2 × 5) from the simulator.
- Otherwise → real mode (`leaderboard_real.build_live_positions`):
  - Up to 5 rows per `(track, car, experiment)` group present in the
    lake (4 fastest historicals + the live driver if it matches the
    group's track/car AND its `experiment` matches the loop variable).
  - A 1-row solo group when the live driver is racing on a
    `(track, car, experiment)` that has no historicals yet.
  - `500 {"detail": "QuixLake credentials missing"}` when
    `QUIXLAKE_URL` or `QUIX_LAKE_TOKEN` aren't set.
  - `500 {"detail": "<upstream error>"}` when the lake query raises.
  - Live-driver absence (consumer can't connect, or no recent ticks)
    is *not* an error — we serve historical-only rows at 200.

## File inventory

### Backend

| File | What it does |
|---|---|
| `test-manager-backend/api/routes/live_positions_sim.py` | Pure simulator. Matrix constants, per-driver splits, lap-roll state machine, sector math, rank pass, ghost interpolation. Public: `make_local_dev_live_positions()`. Also exposes `ghost_time_for_splits`, `rank_group`, `sector_window_from_norm_pos`, and `EQUAL_SPLITS` for the real-mode path. |
| `test-manager-backend/api/routes/leaderboard.py` | Single `GET /leaderboard/live-positions` route. Delegates to the simulator in LOCAL_DEV_MODE; to `leaderboard_real.build_live_positions()` otherwise. Maps `LeaderboardError` → HTTP 500. |
| `test-manager-backend/api/routes/leaderboard_real.py` | Real-mode assembly: QuixLake query (single-level `GROUP BY`, no CTE), Python reduction (drop each session's max-lap in-progress partition, then MIN per driver), Mongo driver-name display-case lookup, live-driver injection via `live_telemetry.get_active_driver()`, group ranking via the simulator's helpers. |
| `test-manager-backend/api/models.py` | Adds `LivePositionEntry` (Pydantic). `LiveDriverState`, `GhostSample`, `GhostReference` remain because `live_telemetry.py` is the real-mode live-driver source. |
| `test-manager-backend/api/live_telemetry.py` | Consumer thread + simulator + segment helpers + per-driver-best historicals cache. Real mode calls `get_active_driver()` for the live row and `get_historicals_cache()` / `refresh_historicals_cache()` for the lake-backed historicals. Cache refresh fires on `ac-telemetry-session` arrival, on consumer warm-up, and as a route-layer fallback on cold-start cache miss. |
| `test-manager-backend/api/app.py` | **Unchanged.** Still calls `live_telemetry.start()` / `stop()` on lifespan — no-op when `LIVE_TELEMETRY_ENABLED!=true`. |

### Frontend

| File | What it does |
|---|---|
| `test-manager-frontend/types/leaderboard.ts` | `LivePositionEntry` type (track, car, experiment, driver, best_lap_ms, **best_lap_number**, is_active, current_lap, current_lap_time_ms, rank). |
| `test-manager-frontend/lib/api/leaderboard.ts` | `getLivePositions(): Promise<LivePositionEntry[]>`. |
| `test-manager-frontend/lib/hooks/use-live-positions.ts` | 3.5 s poll loop. Re-derives `tracks`, `cars`, `experiments` distinct lists for the dropdowns. |
| `test-manager-frontend/components/analysis/leaderboard-tab.tsx` | Page-level: 3 dropdowns + conditional layout. When at least one row in the filtered group has `is_active: true` (`hasActive=true`), renders the two-table grid (`grid-cols-[3fr_2fr]`). Otherwise renders only the Best Laps table inside a `max-w-3xl mx-auto` wrapper with the subtitle `"No live session right now — showing historical best laps"`. `LivePositionsTable` is never rendered without an active driver. |
| `test-manager-frontend/components/analysis/live-positions-table.tsx` | "Live Sector Comparison" table — Rank / Driver / Best Lap / At Position. Active row tinted (`bg-accent/10`) + LIVE badge + "Lap N" label. Color cue on At Position is by rank-vs-active: row.rank < active.rank → emerald, > → rose. Active row's At Position is extrapolated client-side at 200 ms between polls via a `useRef` anchor `(serverElapsedMs, localT0)` + `setInterval`. Best Lap cell renders `m:ss.SSS (L{N})` via the exported `BestLapCell` helper. |
| `test-manager-frontend/components/analysis/best-laps-table.tsx` | "Best Laps" table — Rank / Driver / Best Lap. Same payload sorted by `best_lap_ms` ascending (null treated as `+Infinity`), ranked 1..N fresh. Active driver keeps LIVE badge; no Lap-N label, no color cue. |

### Deleted

- `test-manager-frontend/lib/hooks/use-ghost-lap.ts`
- `test-manager-frontend/components/analysis/segment-breakdown-table.tsx`

## Integration points

- **Analysis page (`app/analysis/page.tsx`)** is unchanged. It still
  imports `LeaderboardTab` from `components/analysis/leaderboard-tab`.
- **`useLeaderboardApi`** in `lib/hooks/use-api.ts` is preserved but
  now exposes `getLivePositions` only.
- **Real-mode envelope (now implemented in `leaderboard_real.py`)**:
  single-level `GROUP BY session_id, lap, driver, track, carModel,
  experiment` over `ac_telemetry` (no `WITH …` — QuixLake silently
  returns 0 rows for CTEs). The per-driver best is reduced in Python
  after dropping each session's max-lap partition (still-in-progress
  lap). The reduction result is cached in
  `live_telemetry._historicals_cache` and refreshed on every
  `ac-telemetry-session` arrival (one lake query per AC session start
  instead of one per HTTP poll). The live driver's state comes from
  `live_telemetry.get_active_driver()`; absence is degraded gracefully
  to historical-only output (no 500).
- **Real-mode env vars (`quix.yaml` Test Manager - Backend block):**
  `LOCAL_DEV_MODE=false`, `LIVE_TELEMETRY_ENABLED=true`, `QUIXLAKE_URL`,
  `QUIX_LAKE_TOKEN` (Secret), `Quix__Sdk__Token` (Secret),
  `Quix__Portal__Api=https://portal-api.dev.quix.io` (never
  `platform.quix.io` — SSL fails). LOCAL_DEV_MODE stays `true` in
  `docker-compose.dev.yml`.

## Trade-offs

- **Polling at 3.5 s, not faster.** Rank shifts only at sector
  boundaries — 3.5 s is short enough that the user always sees the
  transition within one cell-blink. Tighter polling would burn CPU
  without improving the UX.
- **Single global active driver per group.** The sim has Ludvík active
  in every (track, car, experiment); a future multi-active extension
  would simply mark whichever driver is currently feeding telemetry
  for that group.
- **`live_telemetry.py` left in place even though the current route
  doesn't import it.** Intentional: the consumer infra (Kafka thread,
  `_record_message`, stale-detection) is what real-mode will hang off
  of and rewriting it costs nothing in the meantime.

## Verification (LOCAL_DEV_MODE)

1. `GET /api/v1/leaderboard/live-positions` returns 60 entries, each
   with a `rank ∈ [1, 5]`. Exactly one row per (track, car, experiment)
   group has `is_active: true`.
2. A 95 s poll (1.5 s cadence) showed Ludvík's rank in the
   `ks_nurburgring / bmw_1m / baseline` group transition 5 → 2 → 3 → 5
   (lap rollover) → 2 within one lap, and his `best_lap_ms` transition
   from `null` to `89737` at lap completion. Alice's `best_lap_ms`
   stayed byte-identical (`90457`) across all 64 polls.
3. Playwright captured three full-page screenshots ~8 s apart at
   `http://localhost:3000/analysis?tab=leaderboard`. Each shows 5 rows,
   exactly one LIVE badge, and a mix of green/red "At Position" cells.
   Ludvík's rank changed between shots (2 → 3 → 3 in one run).
