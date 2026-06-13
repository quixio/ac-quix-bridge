# AC/ACC Telemetry — Lakehouse Patterns

Reference for the `ac_telemetry` table in Quix Lakehouse. Covers partition layout, telemetry semantic quirks, and canonical SQL patterns. Tool usage, the table-fallback flow, and the hard query rules live in the system prompt — this KB is the SQL/semantic reference.

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

When the user doesn't specify an environment, pick one from `list_partition_combinations` (or ask if several apply) rather than scanning all of them.

### `session_id` — get it from `list_partition_combinations`, NOT from a `SELECT`

`session_id` is a TIMESTAMP-typed partition column, so its two representations differ:

- **Partition-path form** (what a `WHERE` filter must match): `2026-06-05T16:32:20.885Z` — `T` separator, millisecond precision, trailing `Z`. This is exactly what `list_partition_combinations` returns.
- **SELECT/display form**: a `SELECT session_id ...` returns `2026-06-05 16:32:20.885000` — *space* separator, *microsecond* precision, no `Z`.

**Filtering by a SELECTed `session_id` returns 0 rows** — the formats don't match. So always source `session_id` from `list_partition_combinations` and use that string verbatim; never round-trip it through a `SELECT`. If you only have the display form, convert: replace the space with `T`, trim the fractional part to 3 digits, append `Z`.

## Column naming conventions

- **Per-wheel columns** use suffixes `FL`, `FR`, `RL`, `RR` (front-left, front-right, rear-left, rear-right). Example: `tyreTempFL`, `brakeTempRR`, `wheelSlipFL`. Never invent names like `front_left_tyre_temp`.
- **Per-axis columns** use suffixes `_x`, `_y`, `_z`. Example: `velocity_x`, `accG_y`.
- **`normalizedCarPosition`** ranges 0 → 1 over one lap. Use as the x-axis for per-lap overlays across drivers or sessions.

For the full column list with labels and units, consult `kb_ac_channels.md` or call the `get_schema` tool.

## Time columns — pick the right one for the question

| Column | Meaning | Use for |
|---|---|---|
| `iCurrentTime` | Running lap timer (ms); resets to ~0 at each line crossing, peaks just before the next. = AC's on-screen lap time. | **Primary lap time** — `MAX` per clean lap |
| `iLastTime` | The just-completed lap's official time (= `MAX(iCurrentTime)` of the prior lap). Carries the `0` / `2147483647` "no lap" sentinel. | Official lap time / `m:ss` source |
| `timestamp_ms` | Wall-clock capture time (ms since epoch). Monotonic, never resets. | Session-elapsed, gap detection, time-series ordering / x-axis, lap-time **guard** |
| `iBestTime` | Running session-best lap (ms), reset only when a new best is set. | Best-lap shortcut |
| `currentTime`, `lastTime`, `bestTime` | `"mm:ss.SSS"` strings — display only | Never aggregate (lexical sort is wrong) |

## Lap times — `MAX(iCurrentTime)` (matches the AC screen), guard with `timestamp_ms`

For "how long did lap N take", use the sim's own lap timer — `MAX(iCurrentTime)` per partition lap is exactly what AC displayed on screen:

```sql
SELECT driver, session_id, lap, MAX(iCurrentTime) / 1000.0 AS duration_s
FROM ac_telemetry
WHERE environment = 'prague_office' AND track = 'Spa' AND carModel = 'porsche_991ii_gt3_r'
GROUP BY driver, session_id, lap
ORDER BY session_id, lap
```

`iCurrentTime` resets to ~0 at each crossing and peaks just before the next, so `MAX` per partition lap = that lap's official time (validated: matches `timestamp_ms` within ~1 sample on clean laps, and matches `iLastTime`). It excludes paused/menu time, which wall-clock does not. The boundary lag does **not** affect it — `MAX` ignores the trailing post-reset ~0 rows.

**Two failure modes — both only reach excluded laps, or are caught by the guard:**
- **Carryover.** `iCurrentTime` does NOT reset across driver/config switches sharing a `session_id`, so the *first lap of a stint* (the out-lap) inherits the prior accumulated time (70–100 s too long). The clean-lap filter already drops out-laps — but in legacy *glued* sessions a stint's out-lap can be numbered >1, so on **multi-driver sessions** verify against the guard.
- **The `timestamp_ms` guard** (wall-clock `(MAX - MIN)` per lap) — compute it only **when triggered**: the session is shared by multiple drivers/cars, or a lap looks implausible (sanity-check). If `iCurrentTime` and the guard diverge >~1 s on a clean lap: **icur bigger → carryover** (use the guard); **wall-clock bigger → a mid-lap sim pause** inflated it (use `iCurrentTime`). Don't run the guard on every query.

## Filter rules

- **"No lap" sentinels** — these mean "no lap completed in this session yet" and must be filtered before aggregating `iLastTime`/`iBestTime`. **The sentinel is `0` in Assetto Corsa but `2147483647` (INT32_MAX) in ACC.** Filter `iBestTime > 0 AND iBestTime < 2147483647` (same for `iLastTime`) to be safe across both — otherwise an aborted ACC session injects a huge value that survives a bare `> 0` filter. (Note: the `MAX(iCurrentTime)` lap-time method is immune — the sentinel only matters for the `iBestTime`/`iLastTime` shortcuts.)
- **`NA`/`NA` hybrid rows** — some rows have `track = 'NA'` AND `carModel = 'NA'`; these are AC's mid-transition state (loading, menu). Filter `WHERE track <> 'NA' AND carModel <> 'NA'` unless the user specifically wants transitions.

## Clean-lap filter (default for any lap-time aggregate)

Most sessions contain two laps that aren't real racing laps:

1. **Lap 1 — out-lap.** Driver leaves the pit/menu, drives slowly to the line, then crosses it for the first time — an approach lap, not a flying lap (and the one lap the `iCurrentTime` carryover can corrupt).
2. **The last lap of each session — incomplete.** The driver typically stops recording mid-lap. The partition for the highest `lap` value in that session is partial.

A **clean lap** is therefore one with `lap >= 2 AND lap < MAX(lap) per session`. Sessions with fewer than 3 partition laps (most test/probe sessions) contain **zero clean laps** — they get filtered out entirely.

For best / worst / average / consistency queries, default to clean laps only. Always mention the filter in the answer ("excluded out-lap and incomplete final laps") and call out drivers/sessions that ended up with **0 clean laps** so the user knows why they're missing. If the user explicitly asks about lap 1, all laps, or short test sessions, drop the filter for that turn.

## Valid laps only — for best / fastest / leaderboard (match AC's official time)

AC marks a lap invalid (`isValidLap` flips to `0`) when the driver cuts or exceeds track limits, and `iBestTime` / the on-screen best **exclude invalid laps**. A `MAX(iCurrentTime)` ranking that ignores this can crown a cut lap — verified on the lake: a driver's "best" read ~7 s faster than their true best because the fast lap was invalidated. For any **best / fastest / leaderboard** query keep only valid laps with `HAVING ... AND MIN(isValidLap) = 1` (valid iff it never dropped to 0); this makes the result match `iBestTime` exactly. Drop the filter only if the user explicitly wants all laps, including cuts. (For worst/average/consistency it's optional — mention whether cuts are included.)

## Canonical SQL patterns (analysis mode)

All lap-time aggregates use `MAX(iCurrentTime)` for the lap time and the clean-lap subquery for filtering. `iBestTime` is a fallback shortcut for "best lap" only — it cannot give "worst" or stddev.

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
           MAX(iCurrentTime) / 1000.0 AS duration_s
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'Spa'
      AND carModel = 'porsche_991ii_gt3_r'
      AND lap >= 2
    GROUP BY driver, session_id, lap
    HAVING COUNT(*) > 1000      -- reject config-overflow slivers (a real lap is thousands of samples)
       AND MIN(isValidLap) = 1  -- official laps only; a cut/invalidated lap drops to 0
  ) lap_table
  JOIN (
    SELECT driver, session_id, MAX(lap) AS last_lap
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'Spa'
      AND carModel = 'porsche_991ii_gt3_r'
    GROUP BY driver, session_id
  ) last_per_session
    ON lap_table.driver = last_per_session.driver
   AND lap_table.session_id = last_per_session.session_id
  WHERE lap_table.lap < last_per_session.last_lap
) clean
GROUP BY driver
ORDER BY best_s
```

`MIN(iBestTime) FILTER (WHERE iBestTime > 0)` gives the same `best_s` more cheaply since AC validates internally — use it as a shortcut when only **one number per (driver, session) is needed** (e.g. "best lap per driver").

### iBestTime cannot rank or list individual laps

`iBestTime` is a running session-best — it stores the fastest lap recorded SO FAR, not the duration of any individual lap. Within a session it is monotonically non-increasing, so once lap N sets a new best, lap N+1, N+2 ... will all show the same `iBestTime` value until a faster lap arrives.

Consequence: queries like "two fastest laps", "top N laps", "list all laps with their times", or anything that produces one row per lap **cannot use `iBestTime`**. They must use `MAX(iCurrentTime)` with `GROUP BY (session_id, lap)` (add `driver` and `carModel` if scope spans more than one session — `session_id` is shared across drivers and cars).

Wrong:
```sql
SELECT lap, MIN(iBestTime) AS ms
FROM ac_telemetry
WHERE driver = 'tomas'
GROUP BY lap
ORDER BY ms LIMIT 2
```
Returns the same `iBestTime` for every lap from the best-setting lap onward → ties + wrong ranking.

### Clean-lap filter is MANDATORY for per-lap rankings

Every recorded session has two truncated laps:
- **Lap 1** = out-lap (rolling start / pit exit, partial).
- **Last partition lap** = in-lap (session ended mid-lap; duration is truncated to whenever the user pressed escape).

Both look unrealistically fast (often 5–40 s) and corrupt any "fastest lap" ranking. **Filter them out by joining against `MAX(lap)` per session and requiring `lap > 1 AND lap < last_lap`.** This is non-negotiable for any per-lap query.

A *tight* row-count threshold (e.g. `>= 4000`) is NOT a substitute for the JOIN — a real ~108 s lap at 50 Hz has ~5400 rows, but slow laps, telemetry gaps, or sample-rate changes can drop a real lap below it. The JOIN does the out/in-lap removal.

### Config-overflow slivers — recognise, filter, AND explain

A shared `session_id` also picks up a *second* driver as a tiny **sliver**: a few hundred samples, usually a single lap or lap 0/1, with carried-over lap numbers. Cause: the lake's DCM driver-join briefly flips mid-stint, or (pre-2026-06-05, before the source session-trigger fix) the source didn't roll a new `session_id` on a driver switch. Real example from the live lake: a cross-session leaderboard returned a `best_s` of **0.1 s** for one driver — a sliver's bogus sub-second "lap". The clean-lap JOIN does NOT reliably catch these (the sliver's fake laps can fall mid-range, not just at the boundaries).

1. **Filter** with a COARSE per-lap floor *on top of* the JOIN — `HAVING COUNT(*) > 1000` (a real lap is thousands of samples; a sliver is tens to low-hundreds). Keep it coarse so it rejects only slivers, never a borderline real lap. Do **NOT** add an absolute upper duration cap — lap length is track-dependent (a slow driver, or a long track like the Nürburgring Nordschleife at 7–8 min, is legitimate). If one lap is far longer than that driver's others it may include standing/idle time; **flag it as an outlier**, don't hard-drop it. Conversely, the `COUNT > 1000` floor still lets a *partial* real lap through (a lap cut mid-recording: ~30 s where the field runs ~144 s). No absolute threshold is safe in both directions — so **sanity-check the extreme against the field**: if the fastest lap is wildly off the rest (≈ half the next-fastest, or far below the driver's median), treat it as a partial/artifact and exclude or flag it; never report it as the record.
2. **Exclude it, and explain in neutral data-quality terms.** Post-fix, real drivers don't share *valid* laps within one session, so a sliver-only driver is an artifact — exclude it. If the user would otherwise expect that driver, describe the *data condition* — e.g. "driver X has only an incomplete/partial lap here (too little data), so it's excluded from the comparison." Do **NOT** volunteer that it's a bug in the telemetry source/pipeline; users get the data-quality fact, not our internals. (A driver can legitimately run just a few laps; the tell is a sub-lap sliver sharing another driver's session.)

Right (per-lap time via `MAX(iCurrentTime)`, scoped per session, both truncated laps excluded):
```sql
SELECT
    clean.driver,
    clean.session_id,
    clean.lap,
    MAX(clean.iCurrentTime) / 1000.0 AS duration_s
FROM ac_telemetry clean
JOIN (
    SELECT driver, session_id, MAX(lap) AS last_lap
    FROM ac_telemetry
    WHERE driver = 'tomas'
    GROUP BY driver, session_id
) bounds
  ON  clean.driver     = bounds.driver
  AND clean.session_id = bounds.session_id
WHERE clean.driver = 'tomas'
  AND clean.lap > 1
  AND clean.lap < bounds.last_lap
GROUP BY clean.driver, clean.session_id, clean.lap
HAVING COUNT(*) > 1000             -- reject config-overflow slivers
   AND MIN(clean.isValidLap) = 1   -- official (valid) laps only
ORDER BY duration_s
LIMIT 2
```

The leaderboard / consistency examples earlier in this file follow the same pattern (`lap_table` joined against `last_per_session.last_lap`). Reuse that shape; do not invent shortcuts.

Always `GROUP BY` includes `session_id` (and `driver`) whenever the query may touch more than one session — bare `GROUP BY lap` pools unrelated laps from different drivers/sessions that share a lap number.

### 2. Lap-time consistency (stddev across clean laps)

The Lakehouse rejects WITH/CTE — use subqueries.

```sql
SELECT driver,
       COUNT(*) AS clean_laps,
       ROUND(AVG(duration_s), 3)    AS avg_s,
       ROUND(STDDEV(duration_s), 3) AS stddev_s
FROM (
  SELECT lap_table.driver, lap_table.duration_s
  FROM (
    SELECT driver, session_id, lap,
           MAX(iCurrentTime) / 1000.0 AS duration_s
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'Spa'
      AND carModel = 'porsche_991ii_gt3_r'
      AND lap >= 2
    GROUP BY driver, session_id, lap
    HAVING COUNT(*) > 1000      -- reject config-overflow slivers (a real lap is thousands of samples)
       AND MIN(isValidLap) = 1  -- official laps only; a cut/invalidated lap drops to 0
  ) lap_table
  JOIN (
    SELECT driver, session_id, MAX(lap) AS last_lap
    FROM ac_telemetry
    WHERE environment = 'prague_office'
      AND track = 'Spa'
      AND carModel = 'porsche_991ii_gt3_r'
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

### Formatting lap times as `m:ss.mmm`

Users often want `m:ss.mmm` (e.g. `2:24.180`), not raw seconds. Format from the **integer** ms lap time (`MAX(iCurrentTime)`) — never from a rounded seconds value — so it stays exact:

```sql
printf('%d:%06.3f',
       MAX(iCurrentTime) // 60000,            -- whole minutes
       (MAX(iCurrentTime) % 60000) / 1000.0)  -- remaining s.mmm
  AS lap_time
```

`//` is integer division, `%` modulo (DuckDB). A 145 340 ms lap → `2:25.340`. Keep the raw `duration_s` alongside for sorting/aggregation — **sort by the number, display the string**.

**Pitfalls (seen in practice):**
- `printf('%d', x)` requires an **integer** — `MAX(iCurrentTime)//60000` is one; a float (e.g. `seconds/60`) errors with *"Invalid type specifier d"*.
- **Never** `CAST(seconds/60 AS INTEGER)` for the minutes — DuckDB `CAST` **rounds**, not floors (`151.5 → 3`), giving wrong minutes and negative seconds (`3:-28.447`). Always floor via integer `//` on the **ms** value.
- For an **average** time, format from `AVG(iCurrentTime)` (ms) the same way: `printf('%d:%06.3f', CAST(AVG(iCurrentTime) AS BIGINT)//60000, (CAST(AVG(iCurrentTime) AS BIGINT)%60000)/1000.0)`.

### 3. Peak speed per lap

```sql
SELECT driver, session_id, lap,
       ROUND(MAX(speedKmh), 1) AS peak_speed_kmh
FROM ac_telemetry
WHERE environment = 'prague_office'
  AND driver = 'tomas'
  AND track = 'Spa'
  AND lap >= 1
GROUP BY driver, session_id, lap
ORDER BY session_id, lap
```

Substitute other signal columns (e.g. `rpms`, `tyreTempFL`, `brakeTempRR`) to aggregate any telemetry channel per lap.

## Time-field gotchas

These rules apply whenever a query touches the time columns. Skipping them produces silently-wrong durations.

### `iCurrentTime` carryover across driver switches (the one caveat)

`iCurrentTime` is the per-lap timer and resets at each crossing — but it does **not** reset when the same `session_id` is reused across a driver/config switch. So the new driver's *first* partition lap (their out-lap) inherits the prior accumulated value and reads ~70–100 s too long.

**Symptom**: a lap's `MAX(iCurrentTime)` far exceeds its wall-clock `(MAX-MIN) timestamp_ms`.

This only hits the out-lap, which the clean-lap filter already excludes — *except* in legacy glued sessions where the lap counter carried over and the out-lap is numbered >1. So on **multi-driver sessions**, cross-check against the `timestamp_ms` guard: when they disagree by >~1 s, carryover inflated `iCurrentTime` (trust the guard) while a sim pause inflates wall-clock (trust `iCurrentTime`).

### `iLastTime` = the just-completed lap's official time

`iLastTime` holds `iCurrentTime` at the previous crossing — the completed lap's official time (the value AC shows after the line). It equals `MAX(iCurrentTime)` of that lap; attributing it to a lap needs reading it from lap N+1 (a +1 shift), so `MAX(iCurrentTime)` per lap is usually simpler. Same carryover caveat. Filter its `0` / `2147483647` "no lap" sentinel before aggregating.

### Lap 1 is unreliable for time analysis — skip by default

Lap 1 is typically an out-lap (rolling start, pit exit) and additionally absorbs any `iCurrentTime` carryover from earlier session activity. For lap-time aggregates default to `WHERE lap >= 2` unless the user explicitly asks about lap 1. Lap 1 is fine for signal-range queries (top speed, max RPM) — those are not time-derived.

### Lap-boundary partition lag (~80 ms / ~4 samples at 50 Hz)

When a car crosses the start/finish line, three events happen in sequence over ~80 ms:

1. `normalizedCarPosition` wraps from ~0.99 → ~0.001.
2. ~60 ms later, `iCurrentTime` resets to ~0 and `iLastTime` / `iBestTime` are updated.
3. ~20 ms after that, `completedLaps` and the partition `lap` increment.

So the last ~4 samples of a partition `lap=N` (at 50 Hz) are physically already on lap N+1 (low `normalizedCarPosition`). The lag is symmetric — the same ~80 ms slips off the start of `lap=N+1` — so it cancels across consecutive laps in `MAX(timestamp_ms) - MIN(timestamp_ms)` lap-duration calculations. It only matters if a query inspects the precise first/last samples of a partition.

`completedLaps` is AC's own lap counter; partition `lap = completedLaps + 1` (the lap currently being driven).
