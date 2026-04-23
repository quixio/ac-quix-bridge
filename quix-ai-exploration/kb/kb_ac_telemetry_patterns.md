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

## Time columns — use integer-millisecond versions for math

| Column | Meaning |
|---|---|
| `iCurrentTime` | ms elapsed in the CURRENT lap, resets to 0 at each lap crossing |
| `iLastTime` | ms of the most recently completed lap, updates at the crossing |
| `iBestTime` | ms of the best lap in the session so far |
| `currentTime`, `lastTime`, `bestTime` | `"mm:ss.SSS"` formatted **strings** — display only |

**Never order or aggregate by the string fields.** Lexicographic sort on `"1:59.012"` vs `"2:00.050"` produces wrong answers. Always use the `i*Time` columns for ranking, comparison, or math.

## Lap-time gotchas (non-obvious, AC-specific)

### `iLastTime` is aliased across samples

`iLastTime` on a sample tagged `lap = N` holds the time of lap **N-1**, not lap N. AC writes the value once at the lap crossing and leaves it through all subsequent samples.

Two idioms to get per-lap times:

**Simple (preferred for display)** — `iCurrentTime` resets to 0 each lap, so the MAX within (session_id, lap) is that lap's duration. Accuracy ~16 ms off by one sample interval.

```sql
SELECT driver, session_id, lap,
       MAX(iCurrentTime) / 1000.0 AS lap_time_s
FROM ac_telemetry
WHERE environment = 'prague_office' AND lap >= 1
GROUP BY driver, session_id, lap
ORDER BY session_id, lap
```

**Exact** — use `iLastTime` with the `lap - 1` rename to align lap number with the lap it describes.

```sql
SELECT session_id,
       lap - 1                    AS completed_lap,
       MAX(iLastTime) / 1000.0    AS lap_time_s
FROM ac_telemetry
WHERE environment = 'prague_office' AND lap >= 2 AND iLastTime > 0
GROUP BY session_id, lap
ORDER BY session_id, completed_lap
```

## Filter rules

- **Sentinel zeros** — `iLastTime = 0` and `iBestTime = 0` mean "no lap completed in this session yet." Always filter `iLastTime > 0` (or `iBestTime > 0`) before aggregating, or the minimum will report 0 for any aborted session.
- **`NA`/`NA` hybrid rows** — some rows have `track = 'NA'` AND `carModel = 'NA'`; these are AC's mid-transition state (loading, menu). Filter `WHERE track <> 'NA' AND carModel <> 'NA'` unless the user specifically wants transitions.

## Canonical SQL patterns (analysis mode)

These are examples you can adapt when the user asks a computed question. All three use the same partition-filter + sentinel-filter + GROUP BY structure; only the aggregate changes.

### 1. Lap leaderboard (fastest lap per driver)

```sql
SELECT driver,
       MIN(iBestTime) / 1000.0 AS best_lap_s
FROM ac_telemetry
WHERE environment = 'prague_office'
  AND track = 'ks_nurburgring'
  AND carModel = 'bmw_1m'
  AND iBestTime > 0
GROUP BY driver
ORDER BY best_lap_s ASC
```

### 2. Lap-time consistency (stddev across completed laps)

QuixLake only accepts plain `SELECT` — **WITH / CTE is rejected** (`only SELECT allowed`). Use a subquery instead.

```sql
SELECT driver,
       COUNT(*) AS laps,
       ROUND(AVG(lap_time_s), 3)    AS avg_s,
       ROUND(STDDEV(lap_time_s), 3) AS stddev_s
FROM (
  SELECT driver, session_id, lap,
         MAX(iCurrentTime) / 1000.0 AS lap_time_s
  FROM ac_telemetry
  WHERE environment = 'prague_office'
    AND track = 'ks_nurburgring'
    AND carModel = 'bmw_1m'
    AND lap >= 1
  GROUP BY driver, session_id, lap
  HAVING MAX(iCurrentTime) BETWEEN 30000 AND 600000  -- 30 s to 10 min, excludes pauses + incomplete
) lap_times
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
