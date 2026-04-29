# AC Telemetry — QuixLake Patterns

AC-specific reference for the `ac_telemetry` table in QuixLake. Covers partition layout, AC semantic quirks, and canonical SQL patterns. Use together with `kb_quixlake_api.md` (generic API) and `kb_ac_sessions.md` (live session list).

## Table summary

- **Table name**: `ac_telemetry` (one of several tables in the lake; sibling tables `carcolours_*`, `temperature`, `todata` are unrelated and never relevant to AC queries).
- **Source**: Assetto Corsa shared memory. Sample rate is configurable per pipeline.
- **Expected query latency**: narrow partition-filtered SELECT ~500 ms; `SELECT *` for a single session takes 15-22 s due to CSV serialisation — always project only the columns you need.

## Partition columns (always filter on these)

Eight levels, outermost to innermost:

```
environment, test_rig, experiment, driver, track, carModel, session_id, lap
```

Include as many as you know in the `WHERE` clause — each one prunes the file scan. Minimum filter: `WHERE environment = '...'`.

When the user doesn't specify an environment, default to `environment = 'prague_office'`. Don't scan across environments unless explicitly asked.

## Column naming conventions

- **Per-wheel columns** use suffixes `FL`, `FR`, `RL`, `RR` (front-left, front-right, rear-left, rear-right). Example: `tyreTempFL`, `brakeTempRR`, `wheelSlipFL`. Never invent names like `front_left_tyre_temp`.
- **Per-axis columns** use suffixes `_x`, `_y`, `_z`. Example: `velocity_x`, `accG_y`.
- **`normalizedCarPosition`** ranges 0 → 1 over one lap. Use as the x-axis for per-lap overlays across drivers or sessions.

For the full column list with labels and units, consult `kb_ac_channels.md` or call `GET /schema?table=ac_telemetry`.

## Time columns — pick the right one for the question

| Column | Meaning | Use for |
|---|---|---|
| `timestamp_ms` | Wall-clock capture time of the sample (ms since epoch). Never resets. | **Lap durations** (`MAX - MIN` per partition lap) |
| `iCurrentTime` | Running session timer (ms). Resets at lap crossings BUT does **not** reset across driver/config switches that share a `session_id`. | Sub-lap timing within one driver's clean run only |
| `iLastTime` | At any sample, holds the value of `iCurrentTime` at the previous lap crossing. | Rare — see gotchas below |
| `iBestTime` | Running session-best lap (ms), reset only when a new best is set. | Best-lap leaderboards |
| `currentTime`, `lastTime`, `bestTime` | `"mm:ss.SSS"` strings — display only | Never aggregate (lexical sort is wrong) |

## Lap durations — use `timestamp_ms`, not `iCurrentTime`

For "how long did lap N take", **always** compute wall-clock from `timestamp_ms`:

```sql
SELECT driver, session_id, lap,
       (MAX(timestamp_ms) - MIN(timestamp_ms)) / 1000.0 AS duration_s
FROM ac_telemetry
WHERE environment = 'prague_office' AND track = 'ks_nurburgring'
GROUP BY driver, session_id, lap
ORDER BY session_id, lap
```

`MAX(iCurrentTime)` looks tempting (it's the ms since the last lap crossing) but is **wrong** in our data when a `session_id` carries data from more than one driver/config — the timer doesn't reset on driver switches, so the first partition lap of the new driver inherits the prior accumulated time and reports 70-100 s longer than reality. `timestamp_ms` deltas are immune to this.

`iLastTime` is also dangerous: it equals lap N's duration only if `iCurrentTime` started at 0 in lap N. After a driver switch, that assumption breaks.

## Filter rules

- **Sentinel zeros** — `iLastTime = 0` and `iBestTime = 0` mean "no lap completed in this session yet." Always filter `iLastTime > 0` (or `iBestTime > 0`) before aggregating, or the minimum will report 0 for any aborted session.
- **`NA`/`NA` hybrid rows** — some rows have `track = 'NA'` AND `carModel = 'NA'`; these are AC's mid-transition state (loading, menu). Filter `WHERE track <> 'NA' AND carModel <> 'NA'` unless the user specifically wants transitions.

## Clean-lap filter (default for any lap-time aggregate)

Most sessions contain two laps that aren't real racing laps:

1. **Lap 1 — out-lap.** Driver leaves the pit/menu, drives slowly to the line, then crosses it for the first time. `timestamp_ms`-based duration captures this approach time, so lap 1 is always inflated.
2. **The last lap of each session — incomplete.** The driver typically stops recording mid-lap. The partition for the highest `lap` value in that session is partial.

A **clean lap** is therefore one with `lap >= 2 AND lap < MAX(lap) per session`. Sessions with fewer than 3 partition laps (most test/probe sessions) contain **zero clean laps** — they get filtered out entirely.

For best / worst / average / consistency queries, default to clean laps only. Always mention the filter in the answer ("excluded out-lap and incomplete final laps") and call out drivers/sessions that ended up with **0 clean laps** so the user knows why they're missing. If the user explicitly asks about lap 1, all laps, or short test sessions, drop the filter for that turn.

## Canonical SQL patterns (analysis mode)

All time-based aggregates use `timestamp_ms` for duration and the clean-lap subquery for filtering. `iBestTime` is a fallback shortcut for "best lap" only — it cannot give "worst" or stddev.

### 1. Lap leaderboard — best AND worst clean lap per driver

```sql
SELECT driver,
       ROUND(MIN(duration_s), 3) AS best_s,
       ROUND(MAX(duration_s), 3) AS worst_s,
       COUNT(*) AS clean_laps
FROM (
  SELECT lap_table.driver, lap_table.duration_s
  FROM (
    SELECT driver, session_id, lap,
           (MAX(timestamp_ms) - MIN(timestamp_ms)) / 1000.0 AS duration_s
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'ks_nurburgring'
      AND carModel = 'bmw_1m'
      AND lap >= 2
    GROUP BY driver, session_id, lap
  ) lap_table
  JOIN (
    SELECT driver, session_id, MAX(lap) AS last_lap
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'ks_nurburgring'
      AND carModel = 'bmw_1m'
    GROUP BY driver, session_id
  ) last_per_session
    ON lap_table.driver = last_per_session.driver
   AND lap_table.session_id = last_per_session.session_id
  WHERE lap_table.lap < last_per_session.last_lap
) clean
GROUP BY driver
ORDER BY best_s
```

`MIN(iBestTime) FILTER (WHERE iBestTime > 0)` gives the same `best_s` more cheaply since AC validates internally — use it as a shortcut when only "best" is needed.

### 2. Lap-time consistency (stddev across clean laps)

QuixLake rejects WITH/CTE — use subqueries.

```sql
SELECT driver,
       COUNT(*) AS clean_laps,
       ROUND(AVG(duration_s), 3)    AS avg_s,
       ROUND(STDDEV(duration_s), 3) AS stddev_s
FROM (
  SELECT lap_table.driver, lap_table.duration_s
  FROM (
    SELECT driver, session_id, lap,
           (MAX(timestamp_ms) - MIN(timestamp_ms)) / 1000.0 AS duration_s
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'ks_nurburgring'
      AND carModel = 'bmw_1m'
      AND lap >= 2
    GROUP BY driver, session_id, lap
  ) lap_table
  JOIN (
    SELECT driver, session_id, MAX(lap) AS last_lap
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'ks_nurburgring'
      AND carModel = 'bmw_1m'
    GROUP BY driver, session_id
  ) last_per_session
    ON lap_table.driver = last_per_session.driver
   AND lap_table.session_id = last_per_session.session_id
  WHERE lap_table.lap < last_per_session.last_lap
) clean
GROUP BY driver
HAVING COUNT(*) >= 2
ORDER BY stddev_s ASC
```

### 3. Peak speed per lap

```sql
SELECT driver, session_id, lap,
       ROUND(MAX(speedKmh), 1) AS peak_speed_kmh
FROM ac_telemetry
WHERE environment = 'prague_office'
  AND driver = 'ludvik'
  AND track = 'ks_nurburgring'
  AND lap >= 1
GROUP BY driver, session_id, lap
ORDER BY session_id, lap
```

Substitute other signal columns (e.g. `rpms`, `tyreTempFL`, `brakeTempRR`) to aggregate any telemetry channel per lap.

## Time-field gotchas

These rules apply whenever a query touches the time columns. Skipping them produces silently-wrong durations.

### `iCurrentTime` is the session timer, not lap duration

`iCurrentTime` is AC's running session clock (ms). It is monotonic within an AC sim run but does **not** reset between drivers when the same `session_id` is reused — e.g. if a session was started under driver A's config and the active driver was switched to B mid-session, B's first partition lap inherits A's accumulated `iCurrentTime`. Lap 1 then looks ~70-100 s longer than it really was.

**Symptom**: `MAX(iCurrentTime) - MIN(iCurrentTime)` for a partition lap exceeds wall-clock for that lap.

**Reliable wall-clock duration:**

```sql
SELECT driver, session_id, lap,
       (MAX(timestamp_ms) - MIN(timestamp_ms)) / 1000.0 AS duration_s
FROM ac_telemetry
WHERE environment = 'prague_office' AND track = 'ks_nurburgring'
GROUP BY driver, session_id, lap
ORDER BY duration_s
```

`timestamp_ms` is the wall-clock capture time of each sample and never resets.

### `iLastTime` from lap N+1 only equals lap N's duration if `iCurrentTime` started at 0 in lap N

`iLastTime` holds the value of `iCurrentTime` at the moment the previous lap completed (i.e. the running session timer at finish-line crossing). It equals lap N's wall-clock duration only when `iCurrentTime` started at 0 at the beginning of lap N. After driver/config switches, this assumption breaks. Stick with `timestamp_ms` deltas for duration.

### Lap 1 is unreliable for time analysis — skip by default

Lap 1 is typically an out-lap (rolling start, pit exit) and additionally absorbs any `iCurrentTime` carryover from earlier session activity. For lap-time aggregates default to `WHERE lap >= 2` unless the user explicitly asks about lap 1. Lap 1 is fine for signal-range queries (top speed, max RPM) — those are not time-derived.

### Lap-boundary partition lag (~80 ms / ~4 samples at 50 Hz)

When a car crosses the start/finish line, three events happen in sequence over ~80 ms:

1. `normalizedCarPosition` wraps from ~0.99 → ~0.001.
2. ~60 ms later, `iCurrentTime` resets to ~0 and `iLastTime` / `iBestTime` are updated.
3. ~20 ms after that, `completedLaps` and the partition `lap` increment.

So the last ~4 samples of a partition `lap=N` (at 50 Hz) are physically already on lap N+1 (low `normalizedCarPosition`). The lag is symmetric — the same ~80 ms slips off the start of `lap=N+1` — so it cancels across consecutive laps in `MAX(timestamp_ms) - MIN(timestamp_ms)` lap-duration calculations. It only matters if a query inspects the precise first/last samples of a partition.

`completedLaps` is AC's own lap counter; partition `lap = completedLaps + 1` (the lap currently being driven).
