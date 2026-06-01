# Test-Wide Analysis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual "test-wide" analysis mode that runs the Post-Race Analyzer across every recorded session of a test, returning a cross-session AI summary.

**Architecture:** Optional `session_id` (null = test-wide) on `AnalysisCreate` + `Analysis`. Single runner with branched seed message. Single Quix.AI agent (Post-Race Analyzer) with mode-aware `system_prompt.md` that detects mode from the seed. Frontend gains a mode toggle on the existing AI Summary sub-tab. Schema bumps 1 → 2; optional `session_id` added to `KPI` + `Anomaly` items for attribution. No new endpoints, no new MCP tools, no new agent registration.

**Tech Stack:**
- Backend: Python 3.12, FastAPI, Pydantic, MongoDB (motor), pytest, testcontainers, uv
- Runner: shared `BatchAnalysisAI` class (`shared/post_race_ai/runner.py`)
- Quix.AI: Post-Race Analyzer agent (id `350c788d-d25f-4aea-a78c-61ebab32b059`), KBs `6967dc26-...` (owned) and `0856ac23-...` (shared with QuixLake Querier)
- Frontend: Next.js 14, React, TypeScript, Tailwind, `@tailwindcss/typography`, `remark-gfm`

**Spec reference:** `docs/superpowers/specs/2026-06-01-test-wide-analysis-design.md` (commit `bd21456`).

---

## Phase A — Backend models + API

### Task A1: Loosen `AnalysisCreate.session_id` to optional

**Files:**
- Modify: `test-manager-backend/api/models.py` (`AnalysisCreate` near line 560)
- Test: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Write the failing test**

Append to `test-manager-backend/tests/test_analyses.py`:

```python
def test_create_with_null_session_id_returns_201(client, seed_test):
    """POST /api/v1/analyses accepts session_id=None (test-wide mode)."""
    payload = {"test_id": seed_test["test_id"], "session_id": None}
    r = client.post("/api/v1/analyses", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["session_id"] is None
    assert body["test_id"] == seed_test["test_id"]
    assert body["schema_version"] == 2
```

If `seed_test` fixture doesn't exist verbatim, reuse the existing fixture name from the file (search for `def test_create_analysis` to find the canonical fixture).

- [ ] **Step 2: Run the failing test**

Run from inside the backend dev container (per `feedback_uv_run_on_host` memory):

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py::test_create_with_null_session_id_returns_201 -xvs
```

Expected: FAIL with Pydantic validation error mentioning `session_id` cannot be `None`.

- [ ] **Step 3: Edit `AnalysisCreate`**

In `test-manager-backend/api/models.py`, replace:

```python
class AnalysisCreate(BaseModel):
    """Request body for POST /api/v1/analyses."""

    test_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)
```

with:

```python
class AnalysisCreate(BaseModel):
    """Request body for POST /api/v1/analyses.

    session_id is optional: null = test-wide (analyze every session of the test).
    """

    test_id: str = Field(..., min_length=1)
    session_id: str | None = None
```

- [ ] **Step 4: Edit `Analysis` to allow null session_id + bump schema_version**

In the same file, replace:

```python
class Analysis(BaseModel):
    """Persisted analysis result. One doc per click of Analyze."""

    id: str = Field(..., alias="_id")  # uuid4 string
    schema_version: int = 1  # bump on breaking shape changes
    test_id: str
    session_id: str  # v1 always set
```

with:

```python
class Analysis(BaseModel):
    """Persisted analysis result. One doc per click of Analyze."""

    id: str = Field(..., alias="_id")  # uuid4 string
    schema_version: int = 2  # v2 introduces optional session_id (null = test-wide)
    test_id: str
    session_id: str | None  # null on test-wide rows
```

- [ ] **Step 5: Run the test again**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py::test_create_with_null_session_id_returns_201 -xvs
```

Expected: PASS.

- [ ] **Step 6: Run the full analyses test file to catch regressions**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py -xvs
```

Expected: All pass (existing session-mode tests should keep working — `str | None` accepts both).

- [ ] **Step 7: Commit**

```bash
git add test-manager-backend/api/models.py test-manager-backend/tests/test_analyses.py
git commit -m "feat(tm-backend): make session_id optional on AnalysisCreate (test-wide mode)"
```

---

### Task A2: Add optional `session_id` to `KPI` and `Anomaly`

**Files:**
- Modify: `test-manager-backend/api/models.py` (find `class KPI` and `class Anomaly`)
- Test: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Locate current `KPI` and `Anomaly` definitions**

```bash
grep -n "^class KPI\|^class Anomaly" test-manager-backend/api/models.py
```

Open the file at those line numbers — note the exact current shape.

- [ ] **Step 2: Write the failing test**

Append to `test-manager-backend/tests/test_analyses.py`:

```python
def test_kpi_and_anomaly_session_id_roundtrip(client, complete_analysis_with_attribution):
    """KPI.session_id and Anomaly.session_id survive save → fetch."""
    analysis_id = complete_analysis_with_attribution["analysis_id"]
    r = client.get(f"/api/v1/analyses/{analysis_id}")
    assert r.status_code == 200
    body = r.json()
    kpis = body["kpis"]
    anomalies = body["anomalies"]
    assert any(k.get("session_id") == "2026-06-01T13:13:12.038Z" for k in kpis)
    assert any(a.get("session_id") == "2026-06-01T13:13:12.038Z" for a in anomalies)
```

You may need a fixture that saves an analysis with attributed items. Add this near the other fixtures (top of the same file):

```python
@pytest.fixture
def complete_analysis_with_attribution(client, seed_test):
    """Create a test-wide analysis and save a payload with session_id-attributed items."""
    r = client.post(
        "/api/v1/analyses",
        json={"test_id": seed_test["test_id"], "session_id": None},
    )
    analysis_id = r.json()["id"]
    save_payload = {
        "kpis": [
            {"name": "best_lap_p32", "value": 108.2, "unit": "s",
             "session_id": "2026-06-01T13:13:12.038Z"},
        ],
        "anomalies": [
            {"severity": "warn", "kind": "tire_overheat",
             "description": "FL >100°C", "session_id": "2026-06-01T13:13:12.038Z"},
        ],
        "requirements_check": [],
        "logbook_refs": [],
        "summary_md": "Cross-session comparison.",
        "extra": {},
    }
    client.post(f"/api/v1/analyses/{analysis_id}/save", json=save_payload)
    return {"analysis_id": analysis_id}
```

Note: the save endpoint route may be `/api/v1/analyses/{id}/complete` or similar — verify with:

```bash
grep -nE "@router.(post|put)" test-manager-backend/api/routes/analyses.py
```

Adjust the path in the fixture if needed.

- [ ] **Step 3: Run the failing test**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py::test_kpi_and_anomaly_session_id_roundtrip -xvs
```

Expected: FAIL with validation error "extra field not permitted: session_id" (Pydantic strict mode).

- [ ] **Step 4: Edit `KPI` and `Anomaly`**

Locate `class KPI` in `test-manager-backend/api/models.py` and add the field:

```python
class KPI(BaseModel):
    name: str
    value: float | str | int
    unit: str | None = None
    notes: str | None = None
    session_id: str | None = None  # NEW (v2) — attribution in test-wide mode
```

Locate `class Anomaly` and add the field:

```python
class Anomaly(BaseModel):
    severity: Literal["info", "warn", "error"]
    kind: str
    lap: int | None = None
    time_ms: int | None = None
    description: str
    evidence: str | None = None
    session_id: str | None = None  # NEW (v2) — attribution in test-wide mode
```

(If the exact field order in the existing classes differs, preserve it — just insert `session_id` at the end.)

- [ ] **Step 5: Run the test again**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py::test_kpi_and_anomaly_session_id_roundtrip -xvs
```

Expected: PASS.

- [ ] **Step 6: Run the full test file**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py -xvs
```

Expected: All pass (the new optional field is backwards compatible).

- [ ] **Step 7: Commit**

```bash
git add test-manager-backend/api/models.py test-manager-backend/tests/test_analyses.py
git commit -m "feat(tm-backend): add optional session_id to KPI and Anomaly items"
```

---

### Task A3: Add `session_id_is_null` list filter

**Files:**
- Modify: `test-manager-backend/api/models.py` (`AnalysisListQuery`)
- Modify: `test-manager-backend/api/routes/analyses.py` (list handler)
- Test: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Write the failing test**

Append to `test-manager-backend/tests/test_analyses.py`:

```python
def test_list_filter_by_session_id_is_null(client, seed_test):
    """GET /api/v1/analyses?session_id_is_null=true returns only test-wide docs."""
    # Create one session-mode analysis
    client.post("/api/v1/analyses", json={
        "test_id": seed_test["test_id"],
        "session_id": "2026-06-01T13:13:12.038Z",
    })
    # Create one test-wide analysis
    client.post("/api/v1/analyses", json={
        "test_id": seed_test["test_id"],
        "session_id": None,
    })

    r = client.get(
        "/api/v1/analyses",
        params={"test_id": seed_test["test_id"], "session_id_is_null": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["session_id"] is None
```

- [ ] **Step 2: Run the failing test**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py::test_list_filter_by_session_id_is_null -xvs
```

Expected: FAIL — `session_id_is_null` is ignored, so the list returns both docs.

- [ ] **Step 3: Add the field to `AnalysisListQuery`**

In `test-manager-backend/api/models.py`, find `class AnalysisListQuery(PaginationParams)` and add:

```python
class AnalysisListQuery(PaginationParams):
    """Query parameters for GET /api/v1/analyses."""

    test_id: str | None = None
    session_id: str | None = None
    session_id_is_null: bool | None = None  # NEW — filter for test-wide history
    status: AnalysisStatus | None = None
    # ... preserve any other existing fields
```

(Match the existing field order; just add `session_id_is_null` after `session_id`.)

- [ ] **Step 4: Wire the filter into the list handler**

Open `test-manager-backend/api/routes/analyses.py`. Find the GET list handler. Where the Mongo filter dict is built (search for `test_id` and `session_id` assignments to a `filter` or `query` dict), add:

```python
if params.session_id_is_null is True:
    mongo_filter["session_id"] = None
```

The exact variable name (`mongo_filter`, `query`, etc.) depends on the current code — use what's there. Place it AFTER the existing `session_id` handling so an explicit `session_id` value takes precedence if both are passed.

- [ ] **Step 5: Run the test again**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analyses.py::test_list_filter_by_session_id_is_null -xvs
```

Expected: PASS.

- [ ] **Step 6: Run the full backend suite**

```bash
docker exec ac-quix-backend uv run pytest tests/ -x
```

Expected: All pass.

- [ ] **Step 7: Run gates (per `feedback_run_full_gates` memory)**

```bash
docker exec ac-quix-backend uv run ruff check .
docker exec ac-quix-backend uv run ruff format --check .
docker exec ac-quix-backend uv run ty check
```

Expected: clean. If `ruff format --check` fails on YOUR new lines only, run `uv run ruff format` and re-stage.

- [ ] **Step 8: Commit**

```bash
git add test-manager-backend/api/models.py test-manager-backend/api/routes/analyses.py test-manager-backend/tests/test_analyses.py
git commit -m "feat(tm-backend): add session_id_is_null list filter for test-wide history"
```

---

## Phase B — Runner

### Task B1: Branch `_seed_message` on `session_id`

**Files:**
- Modify: `shared/post_race_ai/runner.py` (lines 63 + 147–160)
- Test: `test-manager-backend/tests/test_analysis_runner.py`

- [ ] **Step 1: Read the current `run` and `_seed_message`**

```bash
sed -n '60,75p;145,165p' shared/post_race_ai/runner.py
```

Confirm `run` signature is currently:

```python
async def run(self, *, analysis_id: str, test_id: str, session_id: str) -> None:
```

- [ ] **Step 2: Write the failing test**

Append to `test-manager-backend/tests/test_analysis_runner.py`:

```python
def test_seed_message_session_mode_unchanged():
    """Session-mode seed body still mentions session_id and not 'scope: test-wide'."""
    from shared.post_race_ai.runner import BatchAnalysisAI

    runner = BatchAnalysisAI.__new__(BatchAnalysisAI)  # skip __init__
    runner._workspace_id = "ws-x"  # if needed; check actual field name in __init__

    seed = runner._seed_message(
        analysis_id="a1", test_id="TST-0001", session_id="2026-06-01T13:13:12.038Z"
    )
    msg = seed["message"]
    assert "session_id:  2026-06-01T13:13:12.038Z" in msg or "session_id: 2026-06-01T13:13:12.038Z" in msg
    assert "scope:       test-wide" not in msg


def test_seed_message_test_wide_mode():
    """Test-wide-mode seed body uses 'scope: test-wide' and lists the workflow."""
    from shared.post_race_ai.runner import BatchAnalysisAI

    runner = BatchAnalysisAI.__new__(BatchAnalysisAI)
    runner._workspace_id = "ws-x"

    seed = runner._seed_message(
        analysis_id="a1", test_id="TST-0001", session_id=None
    )
    msg = seed["message"]
    assert "scope:       test-wide" in msg
    assert "list_sessions_for_test" in msg
    assert "get_test" in msg
    assert "save_analysis" in msg
```

Note: `_resolved_workspace_id()` is called inside `_seed_message`. If `__new__` without `__init__` causes that to crash, use `monkeypatch.setattr(runner, "_resolved_workspace_id", lambda: "ws-x")` instead inside each test.

- [ ] **Step 3: Run the failing tests**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analysis_runner.py::test_seed_message_session_mode_unchanged tests/test_analysis_runner.py::test_seed_message_test_wide_mode -xvs
```

Expected: FAIL — the current `_seed_message` takes `session_id: str` and the test-wide variant doesn't yet exist.

- [ ] **Step 4: Update `run` signature**

In `shared/post_race_ai/runner.py`, change:

```python
async def run(self, *, analysis_id: str, test_id: str, session_id: str) -> None:
```

to:

```python
async def run(self, *, analysis_id: str, test_id: str, session_id: str | None) -> None:
```

- [ ] **Step 5: Replace `_seed_message` with the branched version**

In the same file, replace the entire current `_seed_message` with:

```python
def _seed_message(
    self, analysis_id: str, test_id: str, session_id: str | None
) -> dict[str, Any]:
    if session_id is None:
        message = (
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
        message = (
            "Analyze the racing session below.\n\n"
            f"analysis_id: {analysis_id}\n"
            f"test_id:     {test_id}\n"
            f"session_id:  {session_id}\n\n"
            "Workspace context: AC telemetry. Default lake table = ac_telemetry_leadboard.\n\n"
            f'Call save_analysis(analysis_id="{analysis_id}", payload={{...}}) exactly once when done.'
        )
    return {
        "message": message,
        "context": {"workspaceId": self._resolved_workspace_id()},
    }
```

(Preserve all imports + class structure around it. Only this method changes.)

- [ ] **Step 6: Run the seed tests again**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analysis_runner.py::test_seed_message_session_mode_unchanged tests/test_analysis_runner.py::test_seed_message_test_wide_mode -xvs
```

Expected: PASS.

- [ ] **Step 7: Run the full runner test file**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analysis_runner.py -xvs
```

Expected: All pass. The signature change should be transparent to existing tests that pass a real `session_id` string.

- [ ] **Step 8: Commit**

```bash
git add shared/post_race_ai/runner.py test-manager-backend/tests/test_analysis_runner.py
git commit -m "feat(runner): branch seed message on session_id for test-wide mode"
```

---

### Task B2: Bump hard timeout 10 → 15 min

**Files:**
- Modify: `shared/post_race_ai/runner.py` (search for `HARD_TIMEOUT_SECONDS`)

- [ ] **Step 1: Locate the constant**

```bash
grep -n "HARD_TIMEOUT_SECONDS\|HARD_TIMEOUT" shared/post_race_ai/runner.py
```

Expected: one or two hits showing `HARD_TIMEOUT_SECONDS = 600`.

- [ ] **Step 2: Change the value**

Edit the line:

```python
HARD_TIMEOUT_SECONDS = 600  # 10 min
```

to:

```python
HARD_TIMEOUT_SECONDS = 900  # 15 min (test-wide can take longer than single-session)
```

If the comment differs, replace it; the value is what matters.

- [ ] **Step 3: Check for related constants that may need updating**

```bash
grep -nE "MAX_POLLS|ORPHAN_THRESHOLD|POLL_INTERVAL" shared/post_race_ai/runner.py test-manager-backend/api/routes/analyses.py
```

If `MAX_POLLS` or an orphan threshold is paired with `HARD_TIMEOUT_SECONDS` (per `project_post_race_ai_summary` memory: previously raised `MAX_POLLS 100→140` to match a 10-min timeout), bump them proportionally. For 15 min, raise `MAX_POLLS` to ~210 (15min × 60s / poll_interval ≈ similar ratio).

If the relationship isn't obvious from the code, leave them as-is and add a TODO comment in the commit message.

- [ ] **Step 4: Run the runner tests**

```bash
docker exec ac-quix-backend uv run pytest tests/test_analysis_runner.py -xvs
```

Expected: pass. Tests usually don't exercise the 15-min wall-clock directly.

- [ ] **Step 5: Commit**

```bash
git add shared/post_race_ai/runner.py
git commit -m "chore(runner): bump HARD_TIMEOUT_SECONDS 600 -> 900 for test-wide mode"
```

---

## Phase C — Agent prompt + KB

### Task C1: Update `system_prompt.md` for branched mode

**Files:**
- Modify: `quix-ai-config/post-race/system_prompt.md`

- [ ] **Step 1: Read the current prompt to know what you're editing**

```bash
cat quix-ai-config/post-race/system_prompt.md
```

Note the existing structure (Hard rules, Workflow, etc.).

- [ ] **Step 2: Replace the opening sentence**

Find:

```
You analyze a single completed racing session in the AC telemetry pipeline. You produce a structured + narrative report and persist it via the `save_analysis` MCP tool.
```

Replace with:

```
You analyze AC telemetry on behalf of Test Manager. **Two modes** depending on what the user message provides:

- `session_id` is set → **session mode**: analyze that single session.
- `scope: test-wide` is set (no session_id) → **test-wide mode**: analyze every session of the test and produce a cross-session report.

You produce a structured + narrative report and persist it via the `save_analysis` MCP tool exactly once.
```

- [ ] **Step 3: Refine Hard Rule 3 (`list_logbook`)**

Find the line:

```
3. Always call `mcp__test-manager__list_logbook` with `include_test_wide=true` on the first turn — pre-session prep notes are relevant context.
```

Replace with:

```
3. Always call `mcp__test-manager__list_logbook` on the first turn:
   - Session mode: `list_logbook(test_id, session_id, include_test_wide=true)`
   - Test-wide mode: `list_logbook(test_id, include_test_wide=true)` (no session_id filter)
```

- [ ] **Step 4: Add a new "Test-wide flow" Hard Rule**

Append a new numbered rule under "## Hard rules" (keep existing numbering intact and add this as the next number). Substitute the actual next index when inserting:

```
N. **Test-wide flow** (when the user message contains `scope: test-wide`):
   a. Call `mcp__test-manager__list_sessions_for_test(test_id)` first — enumerates every recorded session for the test.
   b. Read `Test.requirements` via `mcp__test-manager__get_test(test_id)` — parse what comparison the user wants.
   c. For each session: build partition-filtered queries for the metrics required. Pin the FULL Hive tuple (environment / test_rig / experiment / driver / track / carModel / session_id / lap) on every WHERE clause.
   d. Aggregate cross-session in `summary_md`. **Tag individual `kpis[]` and `anomalies[]` with `session_id`** for attribution.
   e. Per-lap aggregations: exclude lap 1 (out-lap) AND the last partition lap of each session (in-lap, truncated). Use the JOIN against `MAX(lap) AS last_lap` per (driver, session_id) — same shape as the leaderboard query in the patterns KB.
   f. **Cap at 12 sessions per analysis.** If the test has more, analyze the most recent 12 (ORDER BY session_id DESC) and note the truncation in `summary_md`.
```

- [ ] **Step 5: Update the Workflow section**

Find "## Workflow" and after the existing session-mode numbered list, add:

```
### Workflow — test-wide mode

1. Read `analysis_id`, `test_id` from the user message. Confirm the message contains `scope: test-wide`.
2. Call `mcp__test-manager__list_sessions_for_test(test_id)` and `mcp__test-manager__get_test(test_id)`.
3. Call `mcp__test-manager__list_logbook(test_id, include_test_wide=true)`.
4. For each session (up to 12 most recent), query the lake for KPIs scoped to that session_id with the full partition tuple.
5. Aggregate per the test's `requirements` text. Structure `summary_md` around the requirements (one section per requirement). Use markdown tables for variant comparisons.
6. Set `session_id` on each KPI/Anomaly item to attribute it to its source session.
7. Call `mcp__test-manager__save_analysis(analysis_id, payload)` exactly once. Return briefly.
```

- [ ] **Step 6: Update the SQL example**

Find the example line currently reading:

```
sql = "SELECT ... FROM ac_telemetry_leadboard WHERE session_id = '...'"   # always partition-filter; fallback to ac_telemetry on 0 rows
```

Leave it as-is (already correct from prior commit `92ac8f3`).

- [ ] **Step 7: Verify prompt size is under 10,000 chars**

```bash
wc -c quix-ai-config/post-race/system_prompt.md
```

Expected: < 10000. If over, trim less-load-bearing prose (e.g., the "Python analysis environment" section, the `delegate_task` examples — anything not on the critical path for either mode).

- [ ] **Step 8: Commit**

```bash
git add quix-ai-config/post-race/system_prompt.md
git commit -m "feat(post-race-agent): add test-wide mode to system prompt"
```

---

### Task C2: Update `analysis_contract.md` and `tm_schema.md`

**Files:**
- Modify: `quix-ai-config/post-race/kb/analysis_contract.md`
- Modify: `quix-ai-config/post-race/kb/tm_schema.md`

- [ ] **Step 1: Add "Optional session_id attribution" section to `analysis_contract.md`**

Open the file. Find a natural insertion point (after the existing `kpis` and `anomalies` field descriptions, before any "Error cases" or final section). Insert:

```markdown
## Optional `session_id` attribution on items (schema v2)

`KPI` and `Anomaly` items have an optional `session_id` field as of schema v2.

- **Session mode** (`Analysis.session_id` is set): leave each item's `session_id` as `null`. The parent analysis already pins the source session.
- **Test-wide mode** (`Analysis.session_id` is `null`): set `session_id` on every KPI and Anomaly to attribute the metric / issue to its source session. The agent should populate this field for every cross-session item it emits.

Backwards-compatible: pre-v2 docs read `None` for these fields and render unchanged.

## Test-wide payload conventions (when `Analysis.session_id` is `null`)

- `kpis[]` is flat. Name each KPI in an attribution-friendly way when comparing variants:
  - `best_lap_p32psi`, `best_lap_p35psi`, `tire_wear_p32psi_laps`, …
  - Also set the `session_id` field on each KPI item for the underlying source session.
- `requirements_check[]` — one entry per stated requirement, with `evidence` citing cross-session findings.
- `anomalies[]` — pool everything, tag each with its source `session_id`.
- `summary_md` — structure around the test's requirements. Use markdown tables for variant comparisons (the frontend renders them via remark-gfm).
- `logbook_refs[]` — include test-wide entries (logbook entries whose `session_id` is `null`).

The frontend renders schema v1 and v2 docs identically in v1 of the UI — the `session_id` badge on attributed items is opt-in (visible only when set).
```

- [ ] **Step 2: Add test-wide flow note to `tm_schema.md`**

Open `quix-ai-config/post-race/kb/tm_schema.md`. Find a section that discusses MCP tools or workflow (search for `mcp__test-manager__` or `list_sessions_for_test`). Add this short paragraph nearby:

```markdown
### Test-wide flow

When the user message specifies `scope: test-wide` (no `session_id`), call `mcp__test-manager__list_sessions_for_test(test_id)` first to enumerate sessions, then iterate per-session lake queries. Cap at 12 sessions per analysis; if the test has more, analyze the most recent 12 and note the truncation in `summary_md`.
```

If no obvious anchor exists, append it as a new top-level section near the end of the file (before "Related" if such a section exists).

- [ ] **Step 3: Verify both files render plausibly**

Skim each in a markdown viewer or just re-`cat` to eyeball.

- [ ] **Step 4: Commit**

```bash
git add quix-ai-config/post-race/kb/analysis_contract.md quix-ai-config/post-race/kb/tm_schema.md
git commit -m "docs(post-race-agent): document test-wide payload conventions in KBs"
```

---

### Task C3: Push prompt + KBs to Quix.AI portal

**Files:**
- Run: `quix-ai-config/scripts/update_agent.py`
- Run: `quix-ai-config/scripts/update_kb.py`

- [ ] **Step 1: Dry-run the agent push**

```bash
cd quix-ai-config/scripts
uv run update_agent.py --agent post-race --dry-run
```

Expected: a JSON body containing the new `system_prompt` field. Visually confirm "scope: test-wide" appears in the prompt string. `kbAccessRules` should still contain the two KB ids (`6967dc26-...` and `0856ac23-...`).

- [ ] **Step 2: Push the agent prompt**

```bash
uv run update_agent.py --agent post-race
```

Expected: `Updating existing agent 350c788d-d25f-4aea-a78c-61ebab32b059 (Post-Race Analyzer)` and no error.

- [ ] **Step 3: Push `analysis_contract.md`**

```bash
uv run update_kb.py --kb-id 6967dc26-e768-4818-8d93-89e26b33f3ee ../post-race/kb/analysis_contract.md
```

Expected: log lines through `completed (tokens=...)` at ~60-100s. Re-process is automatic per the script.

- [ ] **Step 4: Push `tm_schema.md`**

```bash
uv run update_kb.py --kb-id 6967dc26-e768-4818-8d93-89e26b33f3ee ../post-race/kb/tm_schema.md
```

Expected: same shape — completes after re-processing.

- [ ] **Step 5: Verify the agent has both KBs attached**

```bash
uv run --with httpx --with python-dotenv python -c "
import os, json, httpx, pathlib
from dotenv import load_dotenv
load_dotenv(pathlib.Path('..') / '.env')
portal = os.environ['QUIX_PORTAL_API'].rstrip('/')
with httpx.Client(base_url=portal, headers={'Authorization': f'Bearer {os.environ[\"QUIX_TOKEN\"]}'}, timeout=60.0) as c:
    a = c.get('/ai/api/org/agents/350c788d-d25f-4aea-a78c-61ebab32b059').json()
    print(json.dumps(a.get('kbAccessRules'), indent=2))
"
```

Expected: list contains both `6967dc26-...` and `0856ac23-...` with `accessLevel: standard`.

- [ ] **Step 6: No code to commit (already done in C1 + C2). Move on.**

---

## Phase D — Frontend

### Task D1: Update TypeScript types

**Files:**
- Modify: `test-manager-frontend/types/analysis.ts`

- [ ] **Step 1: Replace the file body**

Open `test-manager-frontend/types/analysis.ts` and update three interfaces:

```typescript
export interface KpiValue {
  name: string;
  value: number | string;
  unit?: string | null;
  notes?: string | null;
  session_id?: string | null;  // NEW (schema v2) — attribution in test-wide mode
}

export interface Anomaly {
  severity: "info" | "warn" | "error";
  kind: string;
  lap?: number | null;
  time_ms?: number | null;
  description: string;
  evidence?: string | null;
  session_id?: string | null;  // NEW (schema v2) — attribution in test-wide mode
}

export interface Analysis {
  id: string;
  schema_version: number;
  test_id: string;
  session_id: string | null;  // CHANGED — null on test-wide rows
  // ... rest unchanged
}

export interface AnalysisCreateRequest {
  test_id: string;
  session_id: string | null;  // CHANGED — null = test-wide
}
```

Leave the rest of the file (AnalysisStatus, ErrorKind, RequirementCheck, AnalysisListResponse) untouched.

- [ ] **Step 2: Type-check the frontend**

```bash
cd test-manager-frontend
npm run type-check
```

Expected: any compile errors point to call sites that assumed `session_id` is non-null. These will be addressed in D2 and D3. Note the errors and proceed; they'll be fixed in the next tasks.

- [ ] **Step 3: Commit the types**

```bash
cd .. # back to repo root
git add test-manager-frontend/types/analysis.ts
git commit -m "feat(tm-frontend): allow null session_id on Analysis types (schema v2)"
```

---

### Task D2: Update `analysesApi` client to support null session filter

**Files:**
- Modify: `test-manager-frontend/lib/api/analyses.ts`

- [ ] **Step 1: Add the `sessionIdIsNull` option**

Edit `analysesApi.list` in `test-manager-frontend/lib/api/analyses.ts`. Update the option type + param mapping:

```typescript
list: (
  opts?: {
    testId?: string;
    sessionId?: string;
    sessionIdIsNull?: boolean;  // NEW — filter for test-wide analyses
    status?: "complete" | "failed" | "in_progress";
    page?: number;
    pageSize?: number;
  },
  token?: string | null,
  refreshToken?: () => Promise<string | null>,
) => {
  const params: Record<string, string | number> = {};
  if (opts?.testId !== undefined) params.test_id = opts.testId;
  if (opts?.sessionId !== undefined) params.session_id = opts.sessionId;
  if (opts?.sessionIdIsNull === true) params.session_id_is_null = "true";
  if (opts?.status !== undefined) params.status = opts.status;
  if (opts?.page !== undefined) params.page = opts.page;
  if (opts?.pageSize !== undefined) params.page_size = opts.pageSize;
  return apiGet<AnalysisListResponse>(
    `/analyses`,
    Object.keys(params).length > 0 ? params : undefined,
    token,
    refreshToken,
  );
},
```

- [ ] **Step 2: Type-check**

```bash
cd test-manager-frontend
npm run type-check
```

Expected: no new errors from this file. (Errors elsewhere from D1 still expected.)

- [ ] **Step 3: Commit**

```bash
cd ..
git add test-manager-frontend/lib/api/analyses.ts
git commit -m "feat(tm-frontend): allow sessionIdIsNull filter on analyses list API"
```

---

### Task D3: Mode toggle + branched POST in `ai-summary-tab.tsx`

**Files:**
- Modify: `test-manager-frontend/app/analysis/ai-summary/ai-summary-tab.tsx`

- [ ] **Step 1: Read the current component**

```bash
sed -n '1,60p' test-manager-frontend/app/analysis/ai-summary/ai-summary-tab.tsx
```

Locate:
- The state hooks (`useState` for selected session, analyses, etc.)
- The fetch effect (`useEffect` calling `analysesApi.list`)
- The Analyze button handler (calls `analysesApi.create`)
- The render JSX (test/session/history pickers)

- [ ] **Step 2: Add mode state with localStorage persistence**

Near the top of the component function body, add:

```typescript
type Mode = "session" | "test-wide";

const lsKey = (testId: string) => `analysis-mode:${testId}`;

const [mode, setMode] = useState<Mode>(() => {
  if (typeof window === "undefined") return "session";
  const stored = window.localStorage.getItem(lsKey(testId));
  return stored === "test-wide" ? "test-wide" : "session";
});

useEffect(() => {
  if (typeof window !== "undefined") {
    window.localStorage.setItem(lsKey(testId), mode);
  }
}, [mode, testId]);
```

(Substitute `testId` with whatever the current component uses to reference the active test — likely a prop or context.)

- [ ] **Step 3: Branch the fetch effect**

Find the existing `useEffect` that calls `analysesApi.list({ testId, sessionId })`. Replace its argument:

```typescript
analysesApi.list(
  mode === "test-wide"
    ? { testId, sessionIdIsNull: true }
    : { testId, sessionId: selectedSessionId },
  token, refreshToken,
)
```

Make sure `mode` is in the effect's dependency array.

- [ ] **Step 4: Branch the Analyze button POST**

Find the Analyze button click handler that calls `analysesApi.create({ test_id, session_id })`. Replace with:

```typescript
const payload: AnalysisCreateRequest = {
  test_id: testId,
  session_id: mode === "test-wide" ? null : selectedSessionId,
};
const result = await analysesApi.create(payload, token, refreshToken);
```

- [ ] **Step 5: Add the mode toggle UI**

In the JSX, above the existing controls (Session picker / History dropdown), add:

```tsx
<div className="mb-4 flex items-center gap-2">
  <span className="text-sm text-muted-foreground">Mode:</span>
  <button
    type="button"
    className={`rounded px-3 py-1 text-sm ${mode === "session" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
    onClick={() => setMode("session")}
  >
    Session
  </button>
  <button
    type="button"
    className={`rounded px-3 py-1 text-sm ${mode === "test-wide" ? "bg-primary text-primary-foreground" : "bg-muted"}`}
    onClick={() => setMode("test-wide")}
  >
    Test-wide
  </button>
</div>
```

(Adjust class names to match the existing Tailwind utility patterns used elsewhere in this component. If the project uses shadcn `Button` or `ToggleGroup` components, prefer those instead of raw `<button>`.)

Then wrap the Session picker so it only renders in session mode:

```tsx
{mode === "session" && (
  <TestSessionPicker
    // existing props
  />
)}
```

The Analyze button label changes per mode:

```tsx
<AnalyzeButton ... label={mode === "test-wide" ? "Analyze test" : "Analyze"} />
```

(If `AnalyzeButton` doesn't take a label prop, pass it via children or update its props — check the component file `components/analyze-button.tsx`.)

- [ ] **Step 6: Type-check + lint + build**

```bash
cd test-manager-frontend
npm run type-check
npm run lint
npm run build
```

Expected: all clean. The build runs Next.js's full type checker (more strict than `tsc --noEmit` for some cases).

- [ ] **Step 7: Local smoke test**

Start the dev stack if not running:

```bash
cd .. # repo root
docker compose -f docker-compose.dev.yml up -d
```

Visit `http://localhost:3000/analysis`. Verify:
- Mode toggle renders.
- Switching to "Test-wide" hides the Session picker and changes the Analyze button label.
- Refresh the page — mode persists per test.

If anything looks broken, fix and re-run gates before committing.

- [ ] **Step 8: Commit**

```bash
git add test-manager-frontend/app/analysis/ai-summary/ai-summary-tab.tsx
git commit -m "feat(tm-frontend): add Session/Test-wide mode toggle on AI Summary tab"
```

---

### Task D4: Render session_id badge on attributed items

**Files:**
- Modify: `test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx`

- [ ] **Step 1: Read the current card**

Locate where `kpis` and `anomalies` are rendered. Search for `kpi.name` and `anomaly.description` to find the JSX blocks.

- [ ] **Step 2: Add a tiny badge component (inline)**

Above the return statement, define:

```tsx
const SessionBadge = ({ sessionId }: { sessionId?: string | null }) => {
  if (!sessionId) return null;
  // Trim to e.g. "2026-06-01 13:13" — drop seconds + Z for visual compactness
  const short = sessionId.replace("T", " ").slice(0, 16);
  return (
    <span className="ml-2 inline-block rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-muted-foreground">
      {short}
    </span>
  );
};
```

- [ ] **Step 3: Render the badge next to each KPI name**

In the KPI list JSX, wherever `{kpi.name}` is rendered as a label, append:

```tsx
{kpi.name}
<SessionBadge sessionId={kpi.session_id} />
```

- [ ] **Step 4: Render the badge next to each anomaly description**

Same pattern in the anomalies list:

```tsx
{anomaly.description}
<SessionBadge sessionId={anomaly.session_id} />
```

- [ ] **Step 5: Type-check + lint + build**

```bash
cd test-manager-frontend
npm run type-check && npm run lint && npm run build
```

Expected: clean.

- [ ] **Step 6: Visual sanity check**

If you have a test-wide analysis already saved in dev Mongo with `session_id` set on items, load `/analysis` and confirm the badge renders. If not, this will be verified end-to-end in Phase E.

- [ ] **Step 7: Commit**

```bash
cd ..
git add test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx
git commit -m "feat(tm-frontend): render session_id badge on attributed KPIs and anomalies"
```

---

## Phase E — Integration test against Tomas's tire-pressure test

### Task E1: End-to-end manual test

**Files:** none (manual procedure).

- [ ] **Step 1: Ensure the dev stack is up**

```bash
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml ps
```

All four containers (mongodb, mock-dcm, backend, frontend) should be `running` / `healthy`.

- [ ] **Step 2: Verify Tomas's tire-pressure test exists in dev TM**

The integration test relies on Tomas's two sessions from 2026-06-01 being present in TM AND in the lake. From `project_post_race_ai_summary` memory, mirror tools live in `~/repos/quix-explorations/test_manager/tools/`. If the test isn't already in dev TM:

```bash
cd ~/repos/quix-explorations/test_manager/tools
uv run mirror_tests_to_postrace.py --test-id <TST-XXXX-tire-pressure>
```

(Use the actual test_id from the leaderboard env. Coordinate with the user if unsure which one.)

Verify via:

```bash
curl http://localhost:8080/api/v1/tests/<TST-XXXX>
```

- [ ] **Step 3: Trigger a session-mode analysis (regression baseline)**

In the frontend, navigate to `/analysis`, ensure Mode = Session, pick one of the two sessions, click Analyze.

Expected:
- Progress card appears
- Status transitions: pending → running → fetching → analyzing → saving → complete
- Card renders with KPIs (best_lap_s ~108s for a clean lap) and a summary_md narrative
- No `session_id` badge on KPIs (session mode = no attribution)
- Duration < 5 min

If this fails, debug session-mode regression first (likely a side effect of Phase A/B changes) before continuing.

- [ ] **Step 4: Trigger a test-wide analysis**

Switch Mode toggle to "Test-wide". Click "Analyze test".

Expected:
- Progress card appears, same status transitions
- Backend logs (`docker compose logs backend -f`) show `[runner] analysis started` with `session_id=None`
- Quix.AI SSE log shows tool calls in this order:
  1. `mcp__test-manager__get_test` or `mcp__test-manager__list_sessions_for_test`
  2. `mcp__test-manager__list_logbook`
  3. Multiple `mcp__quixlake__run_query` calls (one or more per session)
  4. Exactly one `mcp__test-manager__save_analysis`
- Status reaches `complete` within 15 min (likely 3-8 min for 2 sessions)

If it hits `failed` with hard-timeout, inspect Quix.AI session via portal to see where it got stuck.

- [ ] **Step 5: Inspect the saved analysis**

In the frontend:
- KPIs include both session_ids in `session_id` badges (e.g. `2026-06-01 13:13` and `2026-06-01 13:28`)
- KPI names use attribution-friendly naming (`best_lap_session1`, `best_lap_session2`, or similar)
- `summary_md` has a cross-session comparison (markdown table preferred)
- Anomalies (if any) carry session_id attribution
- Status `complete`, duration shown in card footer

In Mongo (directly, for ground truth):

```bash
docker exec ac-quix-mongo mongosh test_manager --eval "
  db.analyses.find({test_id: '<TST-XXXX>', session_id: null}, {kpis: 1, anomalies: 1}).sort({created_at: -1}).limit(1).pretty()
"
```

Expected: `session_id` field present in at least some `kpis[]` and `anomalies[]` items.

- [ ] **Step 6: Verify history dropdown filters correctly**

In Test-wide mode, History dropdown should show ONLY the test-wide analysis you just created (plus any prior ones if you reran). Switch to Session mode + pick a session → History shows session-scoped analyses for that session only. No cross-contamination.

- [ ] **Step 7: Document any surprises**

If the agent's output diverges from the spec (e.g., KPIs not attributed, no cross-session table, agent enumerates more or fewer than the actual sessions), capture the Quix.AI session id and analysis id and surface them to the user. Prompt iteration via `update_agent.py` is the expected loop here.

- [ ] **Step 8: No commit for this task** (manual verification only).

---

## Phase F — Full repo gates + final commit

### Task F1: Full quality gates

- [ ] **Step 1: Backend gates**

```bash
docker exec ac-quix-backend uv run ruff check .
docker exec ac-quix-backend uv run ruff format --check .
docker exec ac-quix-backend uv run ty check
docker exec ac-quix-backend uv run pytest
```

Expected: all clean. The pre-existing `test_leaderboard.py` 22 failures noted in `project_post_race_ai_summary` memory may still fail — they're another dev's; don't touch.

- [ ] **Step 2: Frontend gates**

```bash
cd test-manager-frontend
npm run type-check
npm run lint
npm run format
npm run build
```

Expected: all clean. Per `feedback_frontend_build_in_dev_container` memory, do NOT run `npm run build` inside the live dev container — it collides with `next dev`. Run on host (in a worktree if doing parallel work).

- [ ] **Step 3: Push the branch**

```bash
cd .. # repo root
git push origin feature/sc-72747/build-post-race-ai-analyzer-pipeline
```

(Or rebase onto a fresh branch if creating a separate PR; coordinate with the user.)

---

## Self-Review Notes

- **Spec coverage:** §3 (fresh on lake) — no separate task; it's the natural flow encoded in the agent prompt (C1) and runner seed (B1). §10 testing covered in E1. §11 rollout covered in C3 + E1 + F1.
- **Placeholder scan:** None present. Every code block contains the actual code.
- **Type consistency:** `session_id: str | None` consistent across models (A1), KPI/Anomaly (A2), runner signature (B1), TS types (D1), API client (D2), and component state (D3).
- **No new MCP tools:** confirmed — `list_sessions_for_test` already exists.
- **Backwards compat:** session-mode flows unchanged at every layer. Schema v2 docs accept v1 reads via optional fields.
