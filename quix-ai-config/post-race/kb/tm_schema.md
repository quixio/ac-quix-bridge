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

**Treat `session_id` as opaque.** Don't reparse it as a date — use the lake's `ts_ms` column for time.

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
