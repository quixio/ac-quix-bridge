# Test Manager Schema

The `mcp__test-manager__*` tools expose Test Manager data. Schemas you'll see:

## Test (from `get_test`)

- `test_id` (string): e.g. `"TST-0007"`
- `experiment_id`, `driver`, `pc_device_id`, `test_rig_device_id`, `environment_id` (strings: foreign keys to other entities)
- `driver_name`, `pc_device_name`, `test_rig_device_name`, `environment_name` (resolved display names — already populated)
- `requirements` (free-text string): the human-written requirements for this test. **You parse this into discrete checks.**
- `sessions` (list of SessionInfo): completed runs on this test
- `created_at` (datetime)

### Requirements parsing guidance

The `requirements` field is free text. Examples:
- `"Complete at least 10 laps. Tyre temps below 95°C. No off-track moments."`
- `"Sub 1:46 lap times on dry tyres. Brake fade investigation."`
- `""` (empty — no formal requirements)

Split on sentence boundaries. Each clause = one `RequirementCheck`. Set `met` based on KPIs.

## SessionInfo (from `get_session` or as a subdoc on Test.sessions)

- `session_id` (string, ISO timestamp with millisecond precision and `Z` suffix): e.g. `"2026-05-21T14:32:15.123Z"`
- `track` (string): AC track code, e.g. `"barcelona"`
- `car_model` (string): AC car code, e.g. `"ferrari_488_gt3"`

**Treat `session_id` as opaque.** Don't reparse it as a date — use the lake's `timestamp_ms` column for time.

## LogbookEntry (from `list_logbook`)

- `id` (string, UUID): use this in `logbook_refs`
- `test_id` (string): parent test
- `session_id` (string or null): null means the entry is test-wide (not tied to a specific session)
- `created_at` (datetime): when the note was written
- `content` (string): the driver/engineer's free text

### When to include test-wide entries

Always call `list_logbook` with `include_test_wide=true`. Test-wide entries often contain pre-session prep notes, setup intentions, and post-test reflections that inform your analysis.

## Driver, Device, Environment (from cross-ref lookups)

Optional lookups via `get_driver`, `get_device`, `get_environment` when you need more detail than the resolved name. Typical fields: `name`, `description`, `created_at`. Schema varies — inspect the JSON.

## Historical sessions (from `list_sessions_for_test`, `list_recent_sessions_for_driver`)

For baseline-vs-current comparisons. The recent-for-driver tool returns a flat list across all tests for one driver — useful for finding a comparable past session.

## Test-wide flow

When the user message specifies `scope: test-wide` (no `session_id`), call `mcp__test-manager__list_sessions_for_test(test_id)` first to enumerate sessions, then iterate per-session lake queries. Cap at 12 sessions per analysis; if the test has more, analyze the most recent 12 and note the truncation in `summary_md`.

## Partition mapping — Test Manager → QuixLake AC telemetry tables

**Default lake table: `ac_telemetry_leadboard`** (current sink — all sessions recorded after 2026-05-29). Older sessions are in legacy `ac_telemetry`. If `FROM ac_telemetry_leadboard` returns 0 rows for the user's `session_id`, retry the same query with `FROM ac_telemetry`. Or call `mcp__quixlake__list_session_combinations(table)` to confirm which table holds the session before composing the query.

Both tables share the same Hive partition layout, in order:

```
environment / test_rig / experiment / driver / track / carModel / session_id / lap
```

Always pin every column you know in the WHERE clause. Values come from Test Manager:

| Lake column | Test Manager source field | Transform applied by TM → DCM |
|---|---|---|
| `environment` | `Test.environment_name` | lowercased, spaces → `_`, apostrophes dropped |
| `test_rig` | `Test.test_rig_device_name` | lowercased, spaces → `_` |
| `experiment` | `Test.experiment_id` | as-is (e.g. `TST-0007`) |
| `driver` | `Test.driver` | lowercased |
| `track` | `SessionInfo.track` | as-is (AC code, e.g. `barcelona`) |
| `carModel` | `SessionInfo.car_model` | as-is (AC code, e.g. `ferrari_488_gt3`) |
| `session_id` | `SessionInfo.session_id` | as-is (ISO ms + `Z`) |
| `lap` | per-row from telemetry stream | integer; filter when scoping a single lap |

Example query for a session-scoped KPI (defaults to `ac_telemetry_leadboard`):

```sql
SELECT MIN(iBestTime) AS best_ms
FROM ac_telemetry_leadboard
WHERE environment = 'thermal_lab'
  AND test_rig    = 'rig_a'
  AND experiment  = 'TST-0007'
  AND driver      = 'ludvik'
  AND track       = 'barcelona'
  AND carModel    = 'ferrari_488_gt3'
  AND session_id  = '2026-05-21T14:32:15.123Z'
```

If 0 rows: rerun with `FROM ac_telemetry` (legacy table). Skipping partition columns forces the lake to scan more files — slower and more expensive.
