# Test-Wide Analysis — Design

**Shortcut ticket:** TBD
**Branch:** TBD (off `feature/sc-72747/build-post-race-ai-analyzer-pipeline` or `main` after merge)
**Date:** 2026-06-01
**Status:** Spec — awaiting plan + implementation
**Supersedes:** "Per-test rollup analysis (`session_id: null`)" v2 bullet in `2026-05-21-post-race-ai-summary-design.md` §1

## 1. Goal + scope

Add a manual **test-wide** analysis mode that produces an AI-generated cross-session report covering every recorded session of a test. The existing per-session AI summary stays as-is; users pick a mode (Session ↔ Test-wide) on the AI Summary sub-tab.

Motivating use case: experiments like "Tire pressure" run multiple sessions, each varying a parameter. Per-session analysis answers single-run questions; users also want cross-session comparisons (lap-time per variant, tire damage over distance, tire lifespan per variant, headroom to thermal limits) that only make sense across the test as a whole.

### In scope (v1)

- API: optional `session_id` on `AnalysisCreate` (null = test-wide); `Analysis` doc keeps `session_id` as `str | None`; new list filter `session_id_is_null`.
- Mongo: schema bump 1 → 2; no migration script (Pydantic absorbs absent fields).
- Runner: branched seed message based on `session_id` presence; same SSE/save loop.
- Agent: single Post-Race Analyzer with mode-aware `system_prompt.md` (no new agent registration).
- KB: `analysis_contract.md` updated to document optional `session_id` attribution on KPI/Anomaly items + test-wide payload conventions. `tm_schema.md` notes the test-wide flow.
- Frontend: mode toggle on existing AI Summary sub-tab; branched controls; analysis card renders `session_id` badge on attributed items.
- Hard timeout bump 10 → 15 min (applies to both modes).
- Session cap inside the agent prompt: max 12 sessions per test-wide run.
- Tests: backend pytest (create/list/runner branching) + frontend type-check/build + manual end-to-end against Tomas's tire-pressure test.

### Deferred to later

- Auto-trigger for test-wide analysis (no obvious "test done" signal — tests can span days).
- Replace-on-reanalyze (same open issue exists for session mode; will address both at once).
- Per-session sub-cards in the analysis card (kpis with `session_id` only get a small badge in v1; richer grouped UI is a follow-up).
- Multi-test or comparative analyses (one test at a time for v1).
- Per-test rollup of existing session analyses (we chose "fresh on lake" — see §3).

## 2. Architecture

```
┌── TM frontend ─ /analysis ▸ AI Summary ─────────────────────────────┐
│  Mode toggle: [Session] [Test-wide]   (persisted per test in LS)    │
│                                                                     │
│  Session mode:                       Test-wide mode:                │
│    [Session ▾] [History ▾]             [History ▾]                  │
│    [Analyze ▸]                         [Analyze test ▸]             │
│    ┌─ Analysis card ──┐                ┌─ Analysis card ──┐         │
│    │  …               │                │  cross-session   │         │
│    └──────────────────┘                │  + session badges│         │
│                                        └──────────────────┘         │
└─────────────────────────────────────────────────────────────────────┘
   │ POST /api/v1/analyses                                          ▲
   │   session mode  : {test_id, session_id: "..."}                 │ GET /api/v1/analyses/{id}
   │   test-wide mode: {test_id, session_id: null}                  │ GET /api/v1/analyses?test_id=...&session_id_is_null=true
   ▼                                                                │
┌── test-manager-backend ────────────────────────────────────────────┤
│   create_analysis(payload)                                         │
│   → spawn BatchAnalysisAI.run(analysis_id, test_id, session_id|None)│
│   → asyncio task: SSE; status; save_analysis → Mongo               │
└────────────────────────────────────────────────────────────────────┘
   │ Quix.AI session                          MCP tools (existing)
   ▼                                          ▲
┌── Post-Race Analyzer (same agent, branched prompt) ────────────────┤
│   Seed has session_id set    → SESSION flow (unchanged)            │
│   Seed has scope: test-wide  → TEST-WIDE flow                      │
│                                                                    │
│   TEST-WIDE flow:                                                  │
│     1. list_sessions_for_test(test_id)                             │
│     2. get_test(test_id) → read requirements                       │
│     3. list_logbook(test_id, include_test_wide=True)               │
│     4. Per session: partition-filtered SQL via mcp__quixlake__*    │
│     5. Cross-session aggregation in narrative + tagged kpis        │
│     6. save_analysis(analysis_id, payload) ONCE                    │
└────────────────────────────────────────────────────────────────────┘
```

**Key points:**

- Single agent (no new portal registration).
- Single runner (`session_id: str | None` parameter).
- Single POST endpoint.
- Same `SaveAnalysisPayload` schema with one tiny enhancement (optional `session_id` on KPI + Anomaly items).
- No new MCP tools — `list_sessions_for_test` already exists on the test-manager MCP server.
- Frontend stays in one tab.

## 3. Data source choice

Decided: **fresh analysis directly on the lake.** No dependency on prior per-session analyses existing or being current. Slower (N× the SQL) but always works and gives the agent raw cross-session data to reason over in one Quix.AI session.

Rejected alternatives:

- Rollup of existing per-session analyses — fast but fragile (sessions must be pre-analyzed) and limits the agent to whatever per-session payloads happened to capture.
- Hybrid — added complexity for marginal benefit.

## 4. API + Mongo changes

### `api/models.py`

```python
class AnalysisCreate(BaseModel):
    test_id: str = Field(..., min_length=1)
    session_id: str | None = None  # null = test-wide

class Analysis(BaseModel):
    id: str = Field(..., alias="_id")
    schema_version: int = 2  # bumped from 1
    test_id: str
    session_id: str | None  # null on test-wide rows
    status: Literal["pending", "running", "fetching", "analyzing", "saving", "complete", "failed"]
    # remaining fields unchanged

class KPI(BaseModel):
    name: str
    value: float | str | int
    unit: str | None = None
    notes: str | None = None
    session_id: str | None = None  # NEW — attribution in test-wide mode; null in session mode

class Anomaly(BaseModel):
    severity: Literal["info", "warn", "error"]
    kind: str
    lap: int | None = None
    time_ms: int | None = None
    description: str
    evidence: str | None = None
    session_id: str | None = None  # NEW — same intent
```

### `AnalysisListQuery`

```python
class AnalysisListQuery(PaginationParams):
    test_id: str | None = None
    session_id: str | None = None
    session_id_is_null: bool | None = None  # NEW — filter for test-wide history
    # ... rest unchanged
```

### Route changes (`routes/analyses.py`)

- POST stays open: either shape accepted. Drop the `min_length=1` constraint on `session_id`; the Pydantic optional already handles validation.
- GET list: when `session_id_is_null=true`, add `{"session_id": None}` to the Mongo filter. When false or omitted, behavior unchanged.

### Mongo indexes

Existing `(test_id, session_id, created_at)` index unchanged — MongoDB indexes nulls normally. No partial index needed for MVP. Queries filter by `session_id: None` for test-wide history.

### Backward compatibility

v1 docs (saved before this change) read fine through v2 Pydantic — absent fields default to None. No migration script. Frontend renders v1 and v2 docs identically in v1 of the UI (session_id badge only appears when present).

## 5. Runner changes (`shared/post_race_ai/runner.py`)

### Signature

```python
async def run(self, *, analysis_id: str, test_id: str, session_id: str | None) -> None:
    ...
```

### `_seed_message` branches on `session_id`

```python
def _seed_message(self, analysis_id, test_id, session_id) -> dict[str, Any]:
    if session_id is None:
        body = (
            "Analyze the entire test across ALL its recorded sessions.\n\n"
            f"analysis_id: {analysis_id}\n"
            f"test_id:     {test_id}\n"
            "scope:       test-wide\n\n"
            "Workflow:\n"
            "  1. Call list_sessions_for_test(test_id) to enumerate sessions.\n"
            "  2. Read the test's requirements via get_test(test_id).\n"
            "  3. Pull logbook with list_logbook(test_id, include_test_wide=True).\n"
            "  4. Query the lake per session (partition-filter on full tuple).\n"
            "  5. Compose cross-session insights; tag each KPI/anomaly with session_id.\n"
            "  6. Call save_analysis(analysis_id, payload={...}) exactly once.\n"
        )
    else:
        body = (
            "Analyze the racing session below.\n\n"
            f"analysis_id: {analysis_id}\n"
            f"test_id:     {test_id}\n"
            f"session_id:  {session_id}\n\n"
            "Workspace context: AC telemetry. Default lake table = ac_telemetry_leadboard.\n\n"
            f'Call save_analysis(analysis_id="{analysis_id}", payload={{...}}) exactly once when done.'
        )
    return {"message": body, "context": {"workspaceId": self._resolved_workspace_id()}}
```

### Other runner changes

None. SSE consumer, save detection, status transitions, retry/timeout logic all remain identical.

### Hard timeout

Bump from `HARD_TIMEOUT_SECONDS = 600` (10 min) to `900` (15 min). Same env var applies to both modes; test-wide just uses more of it. Revisit after observing real test-wide runtimes.

## 6. Agent prompt branching

Single `quix-ai-config/post-race/system_prompt.md` with mode-aware structure. Mode is implicit — the seed message contains either `session_id` (session mode) or `scope: test-wide` (test-wide mode).

### Edits to `system_prompt.md`

1. **Top line generalization:**
   > Was: "You analyze a single completed racing session in the AC telemetry pipeline."
   > New: "You analyze AC telemetry on behalf of Test Manager. Two modes depending on what the user message provides — `session_id` set → single-session analysis. `scope: test-wide` → cross-session analysis spanning every session of the test."

2. **Hard Rule 1 (save_analysis) — unchanged.** Both modes save exactly once.

3. **Hard Rule 3 (`list_logbook`) — refined:**
   - Session mode: `list_logbook(test_id, session_id, include_test_wide=True)`
   - Test-wide mode: `list_logbook(test_id, include_test_wide=True)` (no session_id filter)

4. **Hard Rule 4 (partition tuple) — unchanged.** Same partitions; in test-wide mode you pin the full tuple per session for each query.

5. **New Hard Rule (test-wide flow):**

   When `scope: test-wide`:
   - Call `list_sessions_for_test(test_id)` first. Returns every recorded session.
   - Read `Test.requirements` via `get_test(test_id)` — parse what comparison the user wants.
   - For each session: build partition-filtered queries for the metrics required.
   - Aggregate cross-session in `summary_md`. Tag individual `kpis[]` and `anomalies[]` with `session_id` for attribution.
   - Don't include lap 1 or the last partition lap in lap-time aggregates (truncation rule, JOIN against `MAX(lap) AS last_lap`).
   - **Cap at 12 sessions per analysis.** If the test has more, analyze the most recent 12 and note the truncation in `summary_md`.

6. **Output contract (`summary_md` guidance):** for test-wide, structure the narrative around the test's stated requirements. Use markdown tables (already render via remark-gfm) for variant comparisons.

7. **Workflow section:** keep the existing session-mode workflow listing as-is; add a parallel test-wide workflow listing (steps 1–6 above, expressed naturally).

### Prompt size

Current `system_prompt.md`: 6,786 chars. Estimated growth: ~+1,500 chars. Stays well under the 10,000-char Quix.AI hard limit (observed earlier on Querier prompt push).

## 7. KB updates

Two KBs are bound to the Post-Race Analyzer agent:

- **Post Race Summary** (`6967dc26-...`) — owned: `tm_schema.md`, `analysis_contract.md`
- **QuixLake Querier – AC Telemetry** (`0856ac23-...`) — shared with QuixLake Querier: `kb_quixlake_api.md`, `kb_ac_telemetry_patterns.md`, `kb_ac_channels.md`

### `analysis_contract.md` — new sections

1. **"Optional `session_id` attribution on items"**

   KPI and Anomaly items now have an optional `session_id` field. Set it when the metric/issue is sourced from a specific session (test-wide mode). Leave null in single-session mode — it's implicit from `Analysis.session_id`. Backwards-compatible: pre-v2 docs read None.

2. **"Test-wide payload conventions"** (for `Analysis.session_id is None`):

   - `kpis[]` is flat. Use attribution-friendly names when comparing variants — e.g. `best_lap_32psi`, `tire_wear_38psi_laps`. Also set the `session_id` field on each KPI item for the underlying source session.
   - `requirements_check[]` — one entry per stated requirement, `evidence` cites cross-session findings.
   - `anomalies[]` — pool everything, tag each item with its source `session_id`.
   - `summary_md` — structure around the test's requirements. Markdown tables for variant comparisons.
   - `logbook_refs[]` — include test-wide entries (those with `session_id: null` in the logbook).

3. **Schema version note:** "Schema v2 introduces optional `session_id` on items. Frontend renders v1 and v2 identically for MVP."

### `tm_schema.md` — small additions

- Add a short note: "Test-wide flow: call `mcp__test-manager__list_sessions_for_test(test_id)` first to enumerate sessions, then iterate per-session lake queries."
- No partition-mapping changes.

### Shared KBs (`kb_quixlake_api.md`, `kb_ac_telemetry_patterns.md`, `kb_ac_channels.md`)

**No changes.** All the lap-time gotchas, in-lap JOIN exclusion, partition rules, table-fallback flow apply per session whether you're analyzing one or many. Tool reference unchanged — test-wide just calls the tools more times.

### Push order

1. Edit `analysis_contract.md` + `tm_schema.md` locally.
2. `update_kb.py --kb-id 6967dc26-... ../post-race/kb/analysis_contract.md`
3. `update_kb.py --kb-id 6967dc26-... ../post-race/kb/tm_schema.md`
4. `update_agent.py --agent post-race` (pushes the new `system_prompt.md`).

## 8. Frontend

### Affected files (under `test-manager-frontend/app/analysis/`)

- `ai-summary-tab.tsx` — mode toggle, branched controls, branched fetch
- `analysis-card.tsx` — render `session_id` attribution badges on KPIs/anomalies when present
- `analysis-progress.tsx` — unchanged

### Mode toggle

State: `mode: 'session' | 'test-wide'`. Default = `session`. Persisted per `test_id` in `localStorage`.

Layout sketch:

```
┌─ AI Summary ──────────────────────────────────────────┐
│  Mode: [Session] [Test-wide]                          │
│                                                       │
│  ── Session mode ──                                   │
│  Session ▾   History ▾   [Analyze ▸]                  │
│                                                       │
│  ── Test-wide mode ──                                 │
│  History ▾   [Analyze test ▸]                         │
│                                                       │
│  ┌─ Analysis card ──┐                                 │
│  └──────────────────┘                                 │
└───────────────────────────────────────────────────────┘
```

### Fetch logic

- Session mode: `GET /api/v1/analyses?test_id=…&session_id=…` (existing).
- Test-wide mode: `GET /api/v1/analyses?test_id=…&session_id_is_null=true` (new).

### POST trigger

- Session mode: `{test_id, session_id: "..."}` (existing).
- Test-wide mode: `{test_id, session_id: null}` (new).

### Analysis card v2 rendering

For each KPI / Anomaly that has `session_id` set, render a small subtle session badge (`[2026-06-01 13:13]`) next to the name. Click = no-op for MVP. For session-mode analyses (no items have `session_id`), behavior unchanged.

### History dropdown

- Session mode: session-scoped analyses, newest first (current).
- Test-wide mode: test-wide analyses only, newest first.
- No cross-mode merging — keeps each list focused on its own intent.

### No new routes, no new components

Additive changes only — branching inside `ai-summary-tab.tsx` and a small render branch in `analysis-card.tsx`.

## 9. Edge cases

| Case | Behaviour |
|---|---|
| Test has 0 sessions | Agent calls `list_sessions_for_test` → empty list. Save empty kpis/anomalies + `summary_md` = "No sessions recorded for this test." Doc saved, status `complete`. Frontend shows clean empty state. |
| Test has 1 session | Test-wide flow runs anyway. Result is effectively a session analysis but stored with `session_id: null`. Acceptable — user explicitly chose test-wide. |
| Test has >12 sessions | Agent analyzes the most recent 12 (per prompt cap), notes truncation in `summary_md`. Hard cap protects 15-min timeout + token budget. |
| Session in TM but no lake rows | Per-session query returns 0 rows. Agent appends a `severity: warn, kind: missing_telemetry, session_id: <id>` anomaly and proceeds. Doesn't fail the whole analysis. |
| Sessions span both `ac_telemetry` and `ac_telemetry_leadboard` | Agent's existing table-fallback flow handles per-session. Test-wide can mix sources. |
| Requirements field empty | Agent skips `requirements_check[]`. Still produces kpis + anomalies + summary_md. |
| Rerun test-wide analysis | New doc inserted (no replace semantics — same as session mode). History dropdown shows all of them. Open issue for both modes. |
| Concurrent test-wide + session run for same test | Both spawn independently. No shared state. Acceptable. |
| 15-min hard timeout hit | Status flips to `failed` with `error="hard timeout"`. Partial KPIs are NOT persisted. User can retry. |
| Schema v1 doc fetched after migration | Pydantic accepts both shapes (optional fields). Frontend renders identically. No migration script. |
| Test-wide fired against draft/inactive test | Allowed. `get_test` returns the test regardless of active state. |

## 10. Testing

### Backend (`test-manager-backend/tests/`)

1. `test_analyses.py::test_create_with_null_session_id` — POST `{test_id, session_id: null}` → 201, doc has `session_id: None`, `schema_version: 2`.
2. `test_analyses.py::test_list_filter_by_null_session_id` — GET `?session_id_is_null=true` returns only test-wide docs.
3. `test_analyses.py::test_session_kpi_attribution_roundtrip` — save with KPI having `session_id` set → fetch returns it intact.
4. `test_runner.py::test_seed_message_branches_on_session_id` — `_seed_message(session_id=None)` body contains "scope: test-wide" + workflow steps; `_seed_message(session_id="x")` body matches existing session-mode prose.
5. Regression: existing session-mode tests stay green. `session_id: str | None` accepts both forms.

### Frontend

6. `ai-summary-tab.test.tsx` — mode toggle switches controls; POST body changes shape per mode; localStorage persists mode per test_id.
7. `analysis-card.test.tsx` — KPIs with `session_id` render badge; KPIs without don't.

### Manual integration (against dev workspace)

8. Run test-wide on Tomas's tire-pressure test (sessions `2026-06-01T13:13:12.038Z` + `2026-06-01T13:28:03.719Z` in `ac_telemetry_leadboard`). Verify:
   - Agent enumerates both sessions via `list_sessions_for_test`.
   - Per-session lake queries succeed with full partition tuple.
   - KPIs include attribution (e.g. `best_lap` × 2 with distinct `session_id`).
   - `summary_md` includes a cross-session comparison.
   - Status reaches `complete` within 15 min.
9. Run session-mode on one of those sessions — verify behavior unchanged from current.

### Prompt evolution loop

`update_agent.py` push → run analysis → inspect output via TM frontend or direct Mongo fetch → iterate prompt → re-push. Same loop used for prior post-race work.

## 11. Migration / rollout

1. Implement backend + frontend changes on a branch.
2. Bump `schema_version` to 2; ensure Pydantic absorbs v1 docs without error.
3. Push agent prompt + KB edits to Quix.AI portal (mode change is implicit in seed; safe to push before backend ships).
4. Deploy backend + frontend.
5. Smoke test session-mode (regression) and test-wide mode on Tomas's tire-pressure test.
6. Iterate on prompt as needed via `update_agent.py`.

## 12. Related

- `2026-05-21-post-race-ai-summary-design.md` — original spec; this spec lifts the "v2 deferred" rollup item.
- `project_post_race_ai_summary` memory — full feature state, runner location, prompt evolution history.
- `project_quixlake_mcp` memory — lake MCP tools used per session.
- `feedback_commit_style.md` — Conventional Commits with scope (e.g., `feat(tm-backend): allow null session_id on AnalysisCreate`).
- `2026-06-01-auto-session-end-trigger-design.md` — parked auto-trigger spec (untracked, separate concern; test-wide is intentionally manual-only for v1).
