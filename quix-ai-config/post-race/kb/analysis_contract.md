# Analysis Contract

The `save_analysis` MCP tool accepts a `SaveAnalysisPayload` shape. Each field semantics:

## analysis_id (required, string)

The opaque UUID passed to you in the user message. You must pass it back unchanged.

## summary_md (required, Markdown string, min 1 character)

The narrative spine of your analysis. The only required content field. If everything else is uncertain, write `summary_md` and leave other lists empty.

Write **insight only** — interpretation, trends, causes, recommendations, driver feedback. Do NOT restate the raw KPI/anomaly numbers you already emit in `kpis[]` / `anomalies[]`; the UI renders those as cards and chips, so repeating the values in prose is redundant. Reference a metric to explain what it *means*, don't re-list it.

Suggested section headers: `## Pace`, `## Requirements`, `## Anomalies`, `## Driver feedback`, `## Recommendations`.

**Optional sections — omit, don't apologise:**

- **Logbook is optional.** The driver is not required to file notes. If there are no logbook entries, do NOT treat it as a gap, do NOT say you "cannot confirm" something for lack of notes, and do NOT dwell on it. Either omit the `## Driver feedback` section entirely, or add a single light encouragement to log notes next time — nothing more.
- **Empty requirements.** If `Test.requirements` is empty, say plainly there is nothing to check against and move on. Don't invent requirements or speculate about intent.

## kpis (optional, list of KpiValue)

One entry per measurable. `name` is a **display label shown verbatim** in the UI.

### KpiValue shape

- `name` (string, required): **Title Case prose, never snake_case.** Keep wheel suffixes `FL`/`FR`/`RL`/`RR`. Good: `Fastest Clean Lap`, `Top Speed`, `Max Brake Temp FR`, `Throttle/Brake Overlap (Lap 3)`. Bad: `fastest_clean_lap`, `top_speed_kmh`, `max_brake_temp_FR`.
- `value` (number or string, required): e.g. `1.45321` or `"1:45.321"`. Prefer numbers when meaningful.
- `unit` (optional string): a **real unit of measure** only — `"s"`, `"km/h"`, `"°C"`, `"%"`, `"laps"` — or **omit it**. Never use `"lap"` as the unit of a lap *time* (the time string already carries that), and never use `"-"`/`""` as a placeholder — just leave `unit` out.
- `notes` (optional string): caveats, e.g. `"dropped final partial lap + 1 out-of-range outlier"`

### Worked KpiValue example

```json
{"name": "Fastest Clean Lap", "value": "1:45.321", "notes": "lap 6, valid"}
{"name": "Max Brake Temp FR", "value": 612.0, "unit": "°C", "notes": "spike lap 7 entry T1"}
```

## requirements_check (optional, list of RequirementCheck)

One entry per discrete requirement extracted from `Test.requirements` free text.

### RequirementCheck shape

- `requirement` (string, required): the original requirement text or your normalised version
- `met` (true / false / null): null when subjective or undetermined
- `evidence` (optional string): short justification

### Tri-state semantics

- `true`: KPI or telemetry definitively shows the requirement was met
- `false`: definitively not met
- `null`: cannot be verified from telemetry alone (subjective, requires human judgement)

## anomalies (optional, list of Anomaly)

One entry per noteworthy event.

### Anomaly shape

- `severity` (required): one of `info`, `warn`, `error`
- `kind` (string, required): a **short label shown verbatim** in the UI — Title Case words, never snake_case. Good: `Brake Spike`, `Tyre Overheat`, `Telemetry Gap`, `Off Track`. Bad: `brake_spike`, `off_track`.
- `lap` (optional int): lap number
- `time_ms` (optional int): ms from session start
- `description` (string, required): human-readable
- `evidence` (optional string): SQL row or computed value

## logbook_refs (optional, list of strings)

LogbookEntry `id` values you cited in your narrative. Refer to entries by their UUID.

## extra (optional, dict)

Free-form bag for observations that don't fit a defined field. E.g. weather, setup deltas, mechanical notes. Keys are descriptive strings.

## Optional `session_id` attribution on items (schema v2)

`KPI` and `Anomaly` items have an optional `session_id` field as of schema v2.

- **Session mode** (`Analysis.session_id` is set): leave each item's `session_id` as `null`. The parent analysis already pins the source session.
- **Test-wide mode** (`Analysis.session_id` is `null`): set `session_id` on every KPI and Anomaly to attribute the metric / issue to its source session. The agent should populate this field for every cross-session item it emits.

Backwards-compatible: pre-v2 docs read `None` for these fields and render unchanged.

## Test-wide payload conventions (when `Analysis.session_id` is `null`)

- `kpis[]` is flat. Name each KPI in an attribution-friendly way when comparing variants — still Title Case, put the variant in parentheses:
  - `Best Lap (32 psi)`, `Best Lap (35 psi)`, `Tyre Wear (32 psi)`, …
  - Also set the `session_id` field on each KPI item for the underlying source session.
- `requirements_check[]` — one entry per stated requirement, with `evidence` citing cross-session findings.
- `anomalies[]` — pool everything, tag each with its source `session_id`.
- `summary_md` — structure around the test's requirements. Use markdown tables for variant comparisons (the frontend renders them via remark-gfm).
- `logbook_refs[]` — include test-wide entries (logbook entries whose `session_id` is `null`).

The frontend renders schema v1 and v2 docs identically in v1 of the UI — the `session_id` badge on attributed items is opt-in (visible only when set).

## Worked complete example

```json
{
  "analysis_id": "f47ac10b-58cc-...",
  "summary_md": "## Pace\nGood session...\n\n## Requirements\n...",
  "kpis": [
    {"name": "Fastest Clean Lap", "value": "1:45.321", "notes": "lap 6"},
    {"name": "Top Speed", "value": 213.4, "unit": "km/h"}
  ],
  "requirements_check": [
    {"requirement": "Complete 10 laps", "met": true, "evidence": "12 racing laps recorded"},
    {"requirement": "Tyres < 95°C", "met": false, "evidence": "RR peaked 102°C laps 8-9"}
  ],
  "anomalies": [
    {"severity": "warn", "kind": "Brake Spike", "lap": 7, "time_ms": 723000,
     "description": "Brake temp FR jumped to 612°C entering T1"}
  ],
  "logbook_refs": ["lb-uuid-abc"],
  "extra": {"weather": "20°C dry"}
}
```

## Failure modes

- Missing `summary_md` (or empty string) → MCP returns 422 ValidationError
- Wrong `analysis_id` → MCP returns 404
- Calling `save_analysis` twice → MCP returns 409 ("already complete")
