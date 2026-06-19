# Architecture: Leaderboard dropdowns + Best Laps (Step 1.5)

## What this is

Step 1.5 of the leaderboard recovery. Replaces the auto-select-first
dropdown UX (Track + Car + Experiment driven entirely from the
`/live-positions` snapshot) with three cascading dropdowns wired to
direct QuixLake queries, and rebuilds the Best Laps table off a new
REST endpoint. Live Sector Comparison (left table) is unchanged in this
step — Step 2 will re-gate it on the dropdown selection.

## User flow

1. Open the Leaderboard tab. The backend fetches the full list of
   experiments from the lake. **Experiment** dropdown populates;
   **Track** and **Car** dropdowns are disabled.
2. User picks an experiment. Backend fetches the (tracks, cars)
   available for that experiment in two parallel queries. **Track** +
   **Car** dropdowns become enabled and reset to unselected. Best Laps
   panel shows "Select a track and car…".
3. User picks both Track AND Car. Backend runs the per-driver
   best-lap query. **Best Laps** table populates, sorted ascending.
4. Changing Experiment clears Track + Car + Best Laps.

## Backend

Three new REST endpoints under `/api/v1/leaderboard/`, all in
`api/routes/leaderboard_dropdowns.py`. All routes use the same
`read_permission` dependency as the rest of the leaderboard family,
return 500 with `detail=str(e)` on lake / credentials failure, and emit
422 automatically when the required query params are missing.

The module reuses two helpers from `leaderboard_real.py`:
`_format_sql_string` (single-quote escaper) and `_fold_driver_name`
(NFKD ASCII fold). Pulling them in instead of re-implementing keeps the
fold contract single-sourced; if the fold algorithm ever changes, every
leaderboard endpoint changes in lockstep. Driver display names are NOT
sourced from Mongo (removed — see
`docs/architecture-leaderboard-drop-mongo-names.md`); the folded lake
key is Title-Cased for display.

### GET /api/v1/leaderboard/experiments

Returns `list[str]` — distinct non-empty experiments, sorted ascending.

```sql
SELECT DISTINCT experiment FROM ac_telemetry
WHERE experiment IS NOT NULL AND experiment != ''
ORDER BY experiment
```

### GET /api/v1/leaderboard/experiment-options?experiment={experiment}

Returns `{"tracks": list[str], "cars": list[str]}`. Two single-column
distinct queries — cheaper for QuixLake than one cross-product
distinct, and the response shape stays flat for the frontend.

```sql
SELECT DISTINCT track FROM ac_telemetry
WHERE experiment = '{experiment}' AND track IS NOT NULL
ORDER BY track

SELECT DISTINCT carModel FROM ac_telemetry
WHERE experiment = '{experiment}' AND carModel IS NOT NULL
ORDER BY carModel
```

### GET /api/v1/leaderboard/best-laps?experiment&track&car

Returns `list[{"driver": str, "best_lap_ms": int}]`, sorted ascending
by `best_lap_ms`. Driver names come from the lake's folded form
(`str.lower()` + NFKD strip), Title-Cased per word for display
(`"tomas neubauer"` → `"Tomas Neubauer"`) rather than served
raw-lowercase. No Mongo lookup is involved; only the displayed string is
title-cased, the folded key stays the matching key.

```sql
SELECT driver, MIN(iBestTime) FILTER (WHERE iBestTime > 0) AS best_lap_ms
FROM ac_telemetry
WHERE experiment = '{experiment}'
  AND track = '{track}'
  AND carModel = '{car}'
GROUP BY driver
ORDER BY best_lap_ms ASC
```

No `environment` filter — the spec explicitly removed environment from
the dropdowns. If the same (experiment, track, car) tuple has rows in
multiple environments, `MIN` across all of them is the desired
behaviour for this step.

### Why no cache

The dropdown queries fire on user navigation only (open tab, pick
experiment, pick track/car). They are not hot-loop polled — at most one
per user action, three per dropdown sweep. The `/live-positions` cache
exists because that endpoint runs every 3.5 s; these don't.

### Why no CTE

QuixLake silently returns 0 rows for queries that use `WITH …` (see
`feedback_quixlake_no_cte`). All four SQL builders are single-level
`SELECT … GROUP BY` / `SELECT DISTINCT`.

## Frontend

### lib/api/leaderboard.ts

Adds three new methods to `leaderboardApi`:
* `getExperiments(token, refreshToken) → string[]`
* `getExperimentOptions(experiment, …) → { tracks, cars }`
* `getBestLaps(experiment, track, car, …) → BestLapRow[]`

All go through the shared `apiGet` retry/refresh path. The hook
`useLeaderboardApi` (in `lib/hooks/use-api.ts`) already wraps the whole
`leaderboardApi` object — the three new methods are picked up for free.

### components/analysis/leaderboard-tab.tsx

Rewritten. Three independent fetch effects, gated by the cascading
selection state:

```
mount ─────────────────────► getExperiments()
                              │
                              ▼
                       experiment chosen ─► getExperimentOptions(exp)
                                            │
                                            ▼
                                  track + car chosen ─► getBestLaps(exp, t, c)
```

Each effect cancels its in-flight promise on dep-change via a local
`cancelled` flag. Each surface (experiments / options / best-laps) has
its own `loading` + `error` state for narrow UI signalling.

The Live Sector Comparison table on the left still consumes the
`useLiveStream` WebSocket directly. It's unfiltered in this step
(the spec explicitly says "leave unchanged"); Step 2 will re-attach
the dropdown filter.

The auto-select-first `useEffect`s from the previous version are gone
— dropdowns start empty and require explicit selection.

### components/analysis/best-laps-table.tsx

Stripped to the new prop shape:

```ts
interface BestLapsTableProps { rows: { driver: string; best_lap_ms: number }[] }
```

No more `LivePositionEntry` dependency, no auto-animate (Step 2 will
re-add it if needed), no LIVE badge, no collapse window. The table
ranks rows 1..N in order and renders driver name + best lap. The
shared `BestLapCell` formatter from `live-positions-table.tsx` still
renders the `m:ss.SSS` cell.

## File inventory

### Backend

* **`test-manager-backend/api/routes/leaderboard_dropdowns.py`** —
  *new*. Three routes + SQL builders + lake-client + driver-lookup
  reuse from `leaderboard_real`.
* **`test-manager-backend/api/app.py`** — *modified*. Imports the new
  router and includes it under `/api/v1`.

### Frontend

* **`test-manager-frontend/lib/api/leaderboard.ts`** — *modified*. Adds
  `getExperiments`, `getExperimentOptions`, `getBestLaps`.
* **`test-manager-frontend/components/analysis/leaderboard-tab.tsx`** —
  *rewritten*. Cascading-dropdown wiring + best-laps fetch + new panel
  layout.
* **`test-manager-frontend/components/analysis/best-laps-table.tsx`** —
  *rewritten*. New `BestLapRow[]` prop shape.

## Integration with neighbouring features

| Feature | Component | Touched? | Why |
|---|---|---|---|
| `/leaderboard/live-positions` | `leaderboard.py` | No | Unchanged. Still drives Live Sector Comparison via WS. |
| `/leaderboard/live-stream` | `leaderboard_stream.py`, `useLiveStream` | No | Unchanged. Left table still consumes it. |
| Live consumer state | `live_telemetry.py` | No | Out of scope. |
| Driver-name folding | `_fold_driver_name` | Reused | Single source of truth for the fold; display is `folded.title()` (no Mongo). |
| SQL escaper | `_format_sql_string` | Reused | Same escape semantics as `/live-positions`. |

## Step 2 outlook (not in this PR)

* Re-gate Live Sector Comparison on the dropdown selection.
* Restore the LIVE badge + colour cues on the Best Laps row when the
  active driver matches the chosen (experiment, track, car).
* Possibly fold the dropdowns into a single state-machine hook if the
  triple-effect pattern starts duplicating logic across other tabs.
