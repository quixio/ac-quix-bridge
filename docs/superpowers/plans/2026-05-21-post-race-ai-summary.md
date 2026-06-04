# Post-Race AI Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an AI-generated post-race analysis to Test Manager. Users click "Analyze" on a session and receive structured (KPIs / requirements check / anomalies) + narrative (Markdown) reports persisted in a new `analyses` Mongo collection and rendered in a new "AI Summary" sub-tab under the Analysis tab.

**Architecture:** TM backend `POST /api/v1/analyses` creates a `pending` Mongo doc and spawns an asyncio task that holds an SSE session against a new Quix.AI "Post-Race Analyzer" agent. The agent autonomously fetches context via a new `/mcp` subrouter inside test-manager-backend (read tools + `save_analysis` write tool), queries the lake via existing `quixlake-mcp`, optionally uses `delegate_task` for code-exec, and calls `save_analysis` to persist the final payload. Frontend polls for status.

**Tech Stack:** Python 3.12 + FastAPI + Pydantic v2 + MongoDB (testcontainers in tests) + `httpx` + `respx` (Quix.AI mocking) + FastMCP. Frontend: Next.js 14 + React + TypeScript + Tailwind + React Hook Form. New: `vitest` for frontend unit tests. Quix.AI agent + KBs + MCP server config managed via Python scripts in new `quix-ai-config/` folder.

**Branch:** `feature/sc-72747/build-post-race-ai-analyzer-pipeline` (off `feature/test-manager`).

**Reference spec:** `docs/superpowers/specs/2026-05-21-post-race-ai-summary-design.md`.

---

## File Structure

### Backend (test-manager-backend)

**Created:**
- `api/analysis_runner.py` — asyncio task that holds Quix.AI SSE for one analysis (~120 LOC)
- `api/routes/analyses.py` — POST/GET/list endpoints (~150 LOC)
- `api/routes/mcp/__init__.py` — FastMCP subrouter mounted at `/mcp` (~40 LOC)
- `api/routes/mcp/instrument.py` — `_instrument_tool` decorator (port from quixlab) (~70 LOC)
- `api/routes/mcp/tools.py` — tool registration loop + `_TOOL_TITLES` map (~50 LOC)
- `api/routes/mcp/handlers/core.py` — `get_test`, `get_session`, `list_logbook` (~70 LOC)
- `api/routes/mcp/handlers/lookups.py` — `get_driver`, `get_device`, `get_environment` (~60 LOC)
- `api/routes/mcp/handlers/history.py` — `list_sessions_for_test`, `list_recent_sessions_for_driver` (~50 LOC)
- `api/routes/mcp/handlers/write.py` — `save_analysis` (~80 LOC)
- `tests/test_analyses.py` — route tests
- `tests/test_mcp_server.py` — MCP tool tests
- `tests/test_analysis_runner.py` — runner tests with respx mock

**Modified:**
- `api/models.py` — add `KpiValue`, `RequirementCheck`, `Anomaly`, `Analysis`, `AnalysisCreate`, `AnalysisListQuery`, `SaveAnalysisPayload`; extend `LogbookEntry`, `LogbookEntryCreate`, `LogbookEntryUpdate` with `session_id`; remove phantom `timestamp` from `LogbookEntryUpdate`
- `api/routes/logbook.py` — accept session_id on create + update; validate session_id ∈ test.sessions; add `?session_id=` + `?include_test_wide=true` query
- `api/routes/tests.py:288` — fix `.sort("timestamp", -1)` → `.sort("created_at", -1)`
- `api/mongo.py` — indices for `analyses` and `logbook (test_id, session_id)`
- `api/app.py` — wire `/api/v1/analyses` router + `/mcp` subrouter + startup orphan sweep
- `api/auth.py:71-79` — `[auth] OK` INFO → DEBUG, `[auth] REJECTED` INFO → WARN
- `tests/test_logbook.py` — extend with session_id behaviour + drift-fix regression

### Frontend (test-manager-frontend)

**Created:**
- `app/analysis/ai-summary/page.tsx` — host component, reads `test_id` / `session_id` / `analysis_id` URL params
- `app/analysis/ai-summary/components/test-session-picker.tsx` — two dropdowns + history selector
- `app/analysis/ai-summary/components/analysis-card.tsx` — KPI grid + reqs pills + anomalies + Markdown
- `app/analysis/ai-summary/components/analyze-button.tsx` — POST + start polling
- `app/analysis/ai-summary/hooks/use-analysis-polling.ts` — 3s polling, backoff to 5s after 60s, cap 100 polls, stop on terminal
- `lib/api/analyses.ts` — client methods `create`, `list`, `get`
- `types/analysis.ts` — TypeScript mirror of backend Analysis Pydantic
- `vitest.config.ts` + `vitest.setup.ts` — vitest config + jest-dom setup
- `__tests__/use-analysis-polling.test.ts` — polling state machine
- `__tests__/test-session-picker.test.tsx` — dropdown default logic
- `__tests__/analysis-card.test.tsx` — KPI/anomaly rendering
- `e2e/ai-summary.spec.ts` — Playwright E2E
- `e2e/logbook-session.spec.ts` — Playwright E2E for logbook session badge

**Modified:**
- `types/test.ts` — extend `LogbookEntry`, `LogbookEntryCreate`, `LogbookEntryUpdate` with `session_id`
- `lib/api/logbook.ts` — pass session_id, accept query params
- `components/tests/logbook-entry-form.tsx` — session dropdown
- `components/tests/logbook-entry-list.tsx` — session badge + comment fix on line 81
- `components/tests/test-detail-card.tsx` — AI Summary button + per-session row buttons
- `app/analysis/page.tsx` — register `ai-summary` sub-tab + drop stub sub-tabs (Per-Corner, Live, Single Run, Notebook)
- `package.json` — add vitest deps + scripts

### quix-ai-config (new top-level folder)

- `README.md` — setup runbook
- `scripts/update_agent.py` — agent config inline + push to Quix.AI
- `scripts/update_kb_resource.py` — push KB markdown
- `scripts/bind_kb_to_agent.py` — bind KBs to agent
- `scripts/register_mcp.py` — register test-manager MCP server in org config
- `scripts/list_agents.py` + `scripts/list_kbs.py` — debug helpers
- `post-race/system_prompt.md` — canonical system prompt
- `post-race/kb/analysis_contract.md` — SaveAnalysisPayload semantics
- `post-race/kb/tm_schema.md` — Test / SessionInfo / LogbookEntry shapes

---

## Phase ordering & commits

7 logical commits per spec §10. Each phase below = one commit. Within a phase, TDD cycles produce the commit incrementally.

| # | Phase | Commit subject |
|---|---|---|
| 1 | Logbook session_id rework + drift fix + tightened auth logging | `Add session_id to logbook entries, fix sort drift` |
| 2 | Analyses Pydantic models + Mongo collection + indices | `Add analyses model and Mongo collection` |
| 3 | Analyses CRUD routes (POST/GET/list/detail) | `Add analyses CRUD routes` |
| 4 | Test Manager MCP server (read tools + save_analysis) | `Add test-manager MCP server with read tools and save_analysis` |
| 5 | Analysis runner (asyncio + Quix.AI SSE + orphan sweep) | `Add analysis runner with Quix.AI SSE consumer` |
| 6 | Frontend AI Summary sub-tab + deep-link + vitest setup | `Add AI Summary sub-tab and analyses frontend` |
| 7 | quix-ai-config folder + scripts + system prompt + KBs | `Add quix-ai-config scripts and post-race agent assets` |

Per spec §9, **TDD discipline**: each task = write failing test → red → minimal impl → green → commit. Per-commit gates = `ruff check + ruff format --check + ty check + focused pytest` (backend) or `npm run lint + type-check + focused vitest` (frontend). Pre-push gates run the full suite.

---

This plan is broken into 7 phases below. Continue reading for Phase 1.

---

# Phase 1 — Logbook session_id rework + drift fix + auth logging

**Goal:** add optional `session_id` FK on `LogbookEntry`, drop the phantom `LogbookEntryUpdate.timestamp` field, fix the broken `.sort("timestamp", ...)` line, tighten `[auth]` logging, surface session dropdown in the frontend logbook form, render session badge in the list.

**Commit at end:** `Add session_id to logbook entries, fix sort drift`

---

### Task 1.1: Add `session_id` field to backend logbook models

**Files:**
- Modify: `test-manager-backend/api/models.py` (the `LogbookEntry`, `LogbookEntryCreate`, `LogbookEntryUpdate` classes around line 139-158)
- Test: `test-manager-backend/tests/test_logbook.py` (extend existing)

- [ ] **Step 1: Write failing test for create-with-session_id**

Add to `tests/test_logbook.py` (assuming the existing fixture creates a test with at least one session in `test.sessions[]`):

```python
def test_create_logbook_entry_with_session_id(client, headers, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        headers=headers,
        json={"content": "Tyre pressures off mid-stint", "session_id": session_id},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == session_id
    assert body["content"] == "Tyre pressures off mid-stint"
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_create_logbook_entry_with_session_id -v`

Expected: FAIL — Pydantic doesn't recognise `session_id` field on `LogbookEntryCreate`, returns 422.

- [ ] **Step 3: Add `session_id` to all three logbook models, drop phantom timestamp**

In `api/models.py`, replace the existing `LogbookEntry`, `LogbookEntryCreate`, `LogbookEntryUpdate` classes (currently around lines 139-158) with:

```python
class LogbookEntry(BaseModel):
    """Represents a single logbook entry for a test."""

    id: str = Field(..., alias="_id")
    test_id: str
    session_id: str | None = None       # NEW — None = test-wide note
    created_at: datetime = Field(default_factory=now)
    content: str


class LogbookEntryCreate(BaseModel):
    """Request model for creating a logbook entry."""

    content: str = Field(..., min_length=1)
    session_id: str | None = None       # NEW


class LogbookEntryUpdate(BaseModel):
    """Request model for updating a logbook entry."""

    content: str | None = Field(default=None, min_length=1)
    session_id: str | None = None       # NEW — explicit set/change/clear
    # NOTE: previous `timestamp` field was a phantom — it never matched any
    # stored doc field. Removed to align Update model with Entry/Create.
```

- [ ] **Step 4: Run test, confirm it passes**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_create_logbook_entry_with_session_id -v`

Expected: PASS.

- [ ] **Step 5: Add regression test that null session_id still works (backward compat)**

```python
def test_create_logbook_entry_without_session_id_is_test_wide(client, headers, seeded_test):
    test_id = seeded_test.test_id
    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        headers=headers,
        json={"content": "Pre-test prep notes"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] is None
    assert body["content"] == "Pre-test prep notes"
```

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_create_logbook_entry_without_session_id_is_test_wide -v`

Expected: PASS (no impl change needed — default value of `None` already handles it).

---

### Task 1.2: Validate session_id ∈ test.sessions[] on POST

**Files:**
- Modify: `test-manager-backend/api/routes/logbook.py` (the `create_logbook_entry` function)
- Test: `test-manager-backend/tests/test_logbook.py`

- [ ] **Step 1: Write failing test for invalid session_id rejection**

```python
def test_create_logbook_entry_rejects_unknown_session_id(client, headers, seeded_test):
    test_id = seeded_test.test_id
    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        headers=headers,
        json={"content": "Note", "session_id": "2099-01-01T00:00:00.000Z"},
    )
    assert response.status_code == 400
    assert "session_id" in response.json()["detail"].lower()
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_create_logbook_entry_rejects_unknown_session_id -v`

Expected: FAIL — currently returns 201 because backend doesn't validate session_id.

- [ ] **Step 3: Add validation to create_logbook_entry**

In `api/routes/logbook.py`, modify `create_logbook_entry` (around line 14-30) so it looks up the test once and checks the session_id:

```python
@router.post("/tests/{test_id}/logbook", response_model=LogbookEntry, response_model_by_alias=False)
def create_logbook_entry(
    test_id: str,
    logbook_entry_data: LogbookEntryCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> LogbookEntry:
    test = mongo.tests.find_one({"_id": test_id})
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    # Validate session_id is one of the test's known sessions (or null = test-wide).
    if logbook_entry_data.session_id is not None:
        known_session_ids = {s["session_id"] for s in test.get("sessions", [])}
        if logbook_entry_data.session_id not in known_session_ids:
            raise HTTPException(
                status_code=400,
                detail=f"session_id '{logbook_entry_data.session_id}' not found on test",
            )

    entry = LogbookEntry(
        _id=str(uuid4()),
        test_id=test_id,
        **logbook_entry_data.model_dump(),
    )
    mongo.logbook.insert_one(entry.model_dump(by_alias=True))
    return entry
```

- [ ] **Step 4: Run test, confirm it passes**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_create_logbook_entry_rejects_unknown_session_id -v`

Expected: PASS.

- [ ] **Step 5: Ensure earlier tests still pass**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py -v`

Expected: All logbook tests green.

---

### Task 1.3: Add session_id filter + include_test_wide query params on GET

**Files:**
- Modify: `test-manager-backend/api/routes/logbook.py` (the `get_logbook_entries` function)
- Test: `test-manager-backend/tests/test_logbook.py`

- [ ] **Step 1: Write failing test for session_id filter**

```python
def test_list_logbook_filters_by_session_id(client, headers, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id

    # Create one entry tied to session, one test-wide
    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "tied", "session_id": session_id})
    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "wide"})

    response = client.get(
        f"/api/v1/tests/{test_id}/logbook?session_id={session_id}",
        headers=headers,
    )
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["content"] == "tied"


def test_list_logbook_include_test_wide(client, headers, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id

    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "tied", "session_id": session_id})
    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "wide"})

    response = client.get(
        f"/api/v1/tests/{test_id}/logbook?session_id={session_id}&include_test_wide=true",
        headers=headers,
    )
    assert response.status_code == 200
    contents = {e["content"] for e in response.json()}
    assert contents == {"tied", "wide"}
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_list_logbook_filters_by_session_id tests/test_logbook.py::test_list_logbook_include_test_wide -v`

Expected: FAIL — current route returns all entries.

- [ ] **Step 3: Add query params + filter logic to get_logbook_entries**

In `api/routes/logbook.py`, modify `get_logbook_entries` (around line 45-52):

```python
@router.get(
    "/tests/{test_id}/logbook",
    response_model=list[LogbookEntry],
    response_model_by_alias=False,
)
def get_logbook_entries(
    test_id: str,
    session_id: str | None = None,
    include_test_wide: bool = False,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> list[LogbookEntry]:
    if session_id is not None:
        if include_test_wide:
            query = {"test_id": test_id, "$or": [{"session_id": session_id}, {"session_id": None}]}
        else:
            query = {"test_id": test_id, "session_id": session_id}
    else:
        query = {"test_id": test_id}

    entries = mongo.logbook.find(query).sort("created_at", 1)
    return [LogbookEntry(**entry) for entry in entries]
```

Note: ordering changed to ascending by `created_at` — agent reads chronologically. UI sorts descending in its own code (existing `.sort()` in `logbook-entry-list.tsx`).

- [ ] **Step 4: Run tests, confirm they pass**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py -v`

Expected: All green.

---

### Task 1.4: Allow `session_id` change on PUT (set/clear/swap)

**Files:**
- Modify: `test-manager-backend/api/routes/logbook.py` (the `update_logbook_entry` function)
- Test: `test-manager-backend/tests/test_logbook.py`

- [ ] **Step 1: Write failing test for setting session_id on an existing test-wide entry**

```python
def test_update_logbook_entry_attach_session_id(client, headers, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id

    # Create a test-wide entry
    created = client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                          json={"content": "later attached"}).json()
    entry_id = created["id"]

    # Update to attach session_id
    updated = client.put(
        f"/api/v1/tests/{test_id}/logbook/{entry_id}",
        headers=headers,
        json={"session_id": session_id},
    )
    assert updated.status_code == 200
    assert updated.json()["session_id"] == session_id


def test_update_logbook_entry_clear_session_id(client, headers, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id

    created = client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                          json={"content": "scoped", "session_id": session_id}).json()
    entry_id = created["id"]

    updated = client.put(
        f"/api/v1/tests/{test_id}/logbook/{entry_id}",
        headers=headers,
        json={"session_id": None},
    )
    assert updated.status_code == 200
    assert updated.json()["session_id"] is None
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_update_logbook_entry_attach_session_id tests/test_logbook.py::test_update_logbook_entry_clear_session_id -v`

Expected: First FAILS (Pydantic rejects unknown field). Second FAILS similarly.

Wait — `LogbookEntryUpdate.session_id: str | None = None` already added in Task 1.1. So actually first test may pass for set, but not for clear because `model_dump(exclude_unset=True)` won't include `session_id: None` if it's an explicit body field unless we use `exclude_none=False`.

Actually with `exclude_unset=True`, an explicit `{"session_id": null}` in JSON IS counted as "set" (Pydantic v2 tracks set fields separately from default). So both should work.

If a test fails, the missing piece is server-side validation on the new session_id value. Add:

- [ ] **Step 3: Add validation in update_logbook_entry for non-null new session_id**

In `api/routes/logbook.py`, modify `update_logbook_entry` (around line 55-76):

```python
@router.put(
    "/tests/{test_id}/logbook/{entry_id}",
    response_model=LogbookEntry,
    response_model_by_alias=False,
)
def update_logbook_entry(
    test_id: str,
    entry_id: str,
    entry_update: LogbookEntryUpdate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> LogbookEntry:
    update_data = entry_update.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Validate session_id (if explicitly set to non-null) belongs to this test.
    if "session_id" in update_data and update_data["session_id"] is not None:
        test = mongo.tests.find_one({"_id": test_id})
        if not test:
            raise HTTPException(status_code=404, detail="Test not found")
        known_session_ids = {s["session_id"] for s in test.get("sessions", [])}
        if update_data["session_id"] not in known_session_ids:
            raise HTTPException(
                status_code=400,
                detail=f"session_id '{update_data['session_id']}' not found on test",
            )

    if not (
        updated_entry := mongo.logbook.find_one_and_update(
            {"_id": entry_id, "test_id": test_id},
            {"$set": update_data},
            return_document=ReturnDocument.AFTER,
        )
    ):
        raise HTTPException(status_code=404, detail="Logbook entry not found")

    return LogbookEntry(**updated_entry)
```

- [ ] **Step 4: Run tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py -v`

Expected: All green.

---

### Task 1.5: Fix `.sort("timestamp", -1)` drift bug in `routes/tests.py`

**Files:**
- Modify: `test-manager-backend/api/routes/tests.py` (line 288)
- Test: `test-manager-backend/tests/test_tests.py` or `tests/test_logbook.py`

- [ ] **Step 1: Write failing test asserting logbook returned with TestFullData is sorted by created_at desc**

```python
def test_get_test_full_data_returns_logbook_sorted_by_created_at_desc(
    client, headers, seeded_test, monotonic_clock
):
    """Regression for the .sort('timestamp', -1) drift bug in tests.py:288."""
    test_id = seeded_test.test_id

    # Create three entries with deterministic ordering
    e1 = client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                     json={"content": "first"}).json()
    e2 = client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                     json={"content": "second"}).json()
    e3 = client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                     json={"content": "third"}).json()

    response = client.get(f"/api/v1/tests/{test_id}/full", headers=headers)
    assert response.status_code == 200
    logbook = response.json()["logbook"]
    assert [e["content"] for e in logbook] == ["third", "second", "first"]
```

If `monotonic_clock` fixture doesn't exist, the entries are created in real chronological order via the default `now()` factory, which is sufficient.

- [ ] **Step 2: Run test, confirm it fails**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_get_test_full_data_returns_logbook_sorted_by_created_at_desc -v`

Expected: FAIL — the `.sort("timestamp", -1)` sorts by a missing field, so order is non-deterministic (often insertion order, but not guaranteed).

- [ ] **Step 3: Fix the sort field name**

In `api/routes/tests.py:288`, change:

```python
for e in mongo.logbook.find({"test_id": test_id}).sort("timestamp", -1)
```

to:

```python
for e in mongo.logbook.find({"test_id": test_id}).sort("created_at", -1)
```

- [ ] **Step 4: Run test, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py::test_get_test_full_data_returns_logbook_sorted_by_created_at_desc -v`

Expected: PASS.

---

### Task 1.6: Add composite Mongo index `(test_id, session_id, created_at desc)`

**Files:**
- Modify: `test-manager-backend/api/mongo.py` (the `_mongo.logbook.create_index(...)` block around line 51)

- [ ] **Step 1: Add the composite index**

In `api/mongo.py`, find the existing `_mongo.logbook.create_index("test_id")` call and replace it with:

```python
_mongo.logbook.create_index("test_id")
_mongo.logbook.create_index([("test_id", 1), ("session_id", 1), ("created_at", -1)])
```

The single-field `test_id` index stays for legacy compatibility (cheap, used by other paths). The composite supports the new filtered queries.

- [ ] **Step 2: Smoke-test the indices exist by running the full logbook test suite**

Run: `docker exec test-manager-backend uv run pytest tests/test_logbook.py -v`

Expected: All green. (Indices are best-effort; smoke test just confirms no errors from index creation at startup.)

---

### Task 1.7: Tighten `[auth]` log levels — `OK` to DEBUG, `REJECTED` to WARN

**Files:**
- Modify: `test-manager-backend/api/auth.py` (around lines 56-80)

- [ ] **Step 1: Adjust two `logger.info(...)` calls to use new levels**

In `api/auth.py`, change the three INFO log lines to:

```python
if authorization is None:
    logger.warning("[auth] %s %s — REJECTED: no Authorization header", permission, path)
    raise HTTPException(status_code=403, detail="Not Allowed")
```

(unchanged otherwise, just `logger.info` → `logger.warning`)

```python
if not ok:
    logger.warning(
        "[auth] %s %s — REJECTED: invalid token (scheme=%s, token=%s)",
        permission, path, scheme, _token_preview(token),
    )
    raise HTTPException(status_code=403, detail="Not Allowed")
```

(unchanged otherwise, `logger.info` → `logger.warning`)

```python
logger.debug(
    "[auth] %s %s — OK (scheme=%s, token=%s)",
    permission, path, scheme, _token_preview(token),
)
return None
```

(`logger.info` → `logger.debug`)

- [ ] **Step 2: Run the full backend test suite to confirm no log assertions broke**

Run: `docker exec test-manager-backend uv run pytest -v`

Expected: All green.

---

### Task 1.8: Frontend — extend logbook types + API client with session_id

**Files:**
- Modify: `test-manager-frontend/types/test.ts`
- Modify: `test-manager-frontend/lib/api/logbook.ts`

- [ ] **Step 1: Update LogbookEntry TypeScript types**

In `types/test.ts`, find the existing `LogbookEntry`, `LogbookEntryCreate`, `LogbookEntryUpdate` type declarations and update them. Example shape (adjust to match the actual file's syntax conventions):

```typescript
export interface LogbookEntry {
  id: string;
  test_id: string;
  session_id: string | null;       // NEW
  created_at: string;
  content: string;
}

export interface LogbookEntryCreate {
  content: string;
  session_id?: string | null;      // NEW — optional
}

export interface LogbookEntryUpdate {
  content?: string;
  session_id?: string | null;      // NEW — optional, explicit null clears
}
```

- [ ] **Step 2: Update API client to pass session_id + query params**

In `lib/api/logbook.ts`, update the `create`, `update`, and `list` (or whatever name is used) methods so they pass `session_id` on the create body and accept `session_id` + `include_test_wide` query params on list. Match the existing pattern in the file. Example:

```typescript
async function list(testId: string, opts?: { sessionId?: string; includeTestWide?: boolean }) {
  const params = new URLSearchParams();
  if (opts?.sessionId) params.set("session_id", opts.sessionId);
  if (opts?.includeTestWide) params.set("include_test_wide", "true");
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiGet<LogbookEntry[]>(`/tests/${testId}/logbook${qs}`);
}
```

(Use the existing helper names from the file rather than `apiGet` if they differ.)

- [ ] **Step 3: Type-check frontend**

Run: `docker exec test-manager-frontend npm run type-check`

Expected: 0 errors.

---

### Task 1.9: Frontend — session dropdown in logbook form

**Files:**
- Modify: `test-manager-frontend/components/tests/logbook-entry-form.tsx`

- [ ] **Step 1: Add session prop + dropdown to form**

Replace the contents of `components/tests/logbook-entry-form.tsx` with a version that takes `sessions: SessionInfo[]` as a prop and renders a Select. Use the existing UI primitives (`@/components/ui/select` or similar — check existing imports in the file for the right one):

```typescript
"use client";

import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import * as z from "zod";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type {
  LogbookEntry,
  LogbookEntryCreate,
  LogbookEntryUpdate,
  SessionInfo,
} from "@/types/test";

const TEST_WIDE = "__test_wide__";

const logbookEntrySchema = z.object({
  content: z.string().min(1, "Content is required"),
  session_id: z.string(),  // either TEST_WIDE sentinel or real session_id
});

type FormData = z.infer<typeof logbookEntrySchema>;

interface Props {
  testId: string;
  sessions: SessionInfo[];
  entry?: LogbookEntry;
  onSubmit: (data: LogbookEntryCreate | LogbookEntryUpdate) => Promise<void>;
  onCancel: () => void;
  isSubmitting?: boolean;
}

export function LogbookEntryForm({
  sessions,
  entry,
  onSubmit,
  onCancel,
  isSubmitting = false,
}: Props) {
  // Sort sessions descending by session_id (ISO timestamp string => lexicographic = chronological).
  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.session_id.localeCompare(a.session_id)),
    [sessions],
  );

  const defaultSessionValue = useMemo(() => {
    if (entry?.session_id) return entry.session_id;
    if (sortedSessions.length > 0) return sortedSessions[0].session_id;
    return TEST_WIDE;
  }, [entry, sortedSessions]);

  const { register, handleSubmit, watch, setValue, setFocus, formState: { errors } } =
    useForm<FormData>({
      resolver: zodResolver(logbookEntrySchema),
      defaultValues: {
        content: entry?.content || "",
        session_id: defaultSessionValue,
      },
    });

  useEffect(() => {
    setFocus("content");
  }, [setFocus]);

  const handleFormSubmit = async (data: FormData) => {
    const session_id = data.session_id === TEST_WIDE ? null : data.session_id;
    await onSubmit({ content: data.content, session_id });
  };

  const sessionValue = watch("session_id");

  return (
    <form onSubmit={handleSubmit(handleFormSubmit)} className="space-y-5">
      <div className="space-y-2">
        <Label htmlFor="session_id">Session</Label>
        <Select
          value={sessionValue}
          onValueChange={(v) => setValue("session_id", v, { shouldDirty: true })}
        >
          <SelectTrigger className="w-full" id="session_id">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={TEST_WIDE}>Test-wide</SelectItem>
            {sortedSessions.map((s) => (
              <SelectItem key={s.session_id} value={s.session_id}>
                {s.session_id} · {s.track} / {s.car_model}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {sessions.length === 0 && (
          <p className="text-xs text-muted-foreground mt-1">
            No sessions yet — entry will be test-wide. You can attach it to a session later.
          </p>
        )}
      </div>

      <div className="space-y-2">
        <Label htmlFor="content">Content *</Label>
        <Textarea
          id="content"
          {...register("content")}
          placeholder="Add a remark about the session..."
          rows={4}
        />
        {errors.content && (
          <p className="text-sm text-destructive mt-1.5">{errors.content.message}</p>
        )}
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="outline" onClick={onCancel} disabled={isSubmitting}>
          Cancel
        </Button>
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Saving..." : entry ? "Update Entry" : "Add Entry"}
        </Button>
      </div>
    </form>
  );
}
```

- [ ] **Step 2: Update the parent that mounts this form to pass `sessions`**

In `app/tests/[id]/page.tsx`, find the `<LogbookEntryForm ... />` invocation and add `sessions={test.sessions}` to the props.

- [ ] **Step 3: Type-check frontend**

Run: `docker exec test-manager-frontend npm run type-check`

Expected: 0 errors.

---

### Task 1.10: Frontend — session badge in logbook list + comment fix

**Files:**
- Modify: `test-manager-frontend/components/tests/logbook-entry-list.tsx`

- [ ] **Step 1: Fix the misleading comment on line 81 and add session badge per entry**

In `components/tests/logbook-entry-list.tsx`, change the comment on line 81 from `// Sort entries by timestamp (newest first)` to `// Sort entries by created_at (newest first)`.

Then add a session badge inside each entry card. Inside the existing `<Card>` block, in the header `<div className="flex items-start justify-between gap-2">` section, alongside the `<Clock>` row, add a session pill:

```tsx
<span className={`text-xs px-2 py-0.5 rounded-full ${
  entry.session_id
    ? "bg-primary/10 text-primary"
    : "bg-muted text-muted-foreground"
}`}>
  {entry.session_id ? entry.session_id.slice(0, 16) : "Test-wide"}
</span>
```

(`slice(0, 16)` shows date + hour:minute from ISO string `"2026-05-21T14:32:15.123Z"` → `"2026-05-21T14:32"` — compact + readable.)

- [ ] **Step 2: Type-check + visual-check**

Run: `docker exec test-manager-frontend npm run type-check`

Expected: 0 errors.

Boot the dev stack with `docker compose -f docker-compose.dev.yml up`, open http://localhost:3000, navigate to a test with sessions, create one entry attached to a session and one test-wide. Verify badges render correctly.

---

### Task 1.11: Run full Phase 1 gates + commit

- [ ] **Step 1: Full backend gates**

Run: `docker exec test-manager-backend uv run ruff check . && docker exec test-manager-backend uv run ruff format --check . && docker exec test-manager-backend uv run ty check && docker exec test-manager-backend uv run pytest tests/test_logbook.py -v`

Expected: All green.

- [ ] **Step 2: Frontend gates**

Run: `docker exec test-manager-frontend npm run lint && docker exec test-manager-frontend npm run type-check`

Expected: 0 errors.

- [ ] **Step 3: Commit Phase 1**

```bash
git add test-manager-backend/api/models.py \
        test-manager-backend/api/routes/logbook.py \
        test-manager-backend/api/routes/tests.py \
        test-manager-backend/api/mongo.py \
        test-manager-backend/api/auth.py \
        test-manager-backend/tests/test_logbook.py \
        test-manager-frontend/types/test.ts \
        test-manager-frontend/lib/api/logbook.ts \
        test-manager-frontend/components/tests/logbook-entry-form.tsx \
        test-manager-frontend/components/tests/logbook-entry-list.tsx \
        test-manager-frontend/app/tests/\[id\]/page.tsx

git commit -m "Add session_id to logbook entries, fix sort drift"
```

Expected: clean commit; `git log -1 --oneline` shows the new SHA on `feature/sc-72747/build-post-race-ai-analyzer-pipeline`.

---

**Phase 1 complete.** Logbook entries can be attached to sessions; the drift bug is fixed; auth logs are tightened. Continue to Phase 2 (Analyses Pydantic models + Mongo collection).


---

# Phase 2 — Analyses Pydantic models + Mongo collection

**Goal:** Add the structured + narrative Analysis schema, supporting nested types (KpiValue, RequirementCheck, Anomaly), and Mongo indices. No routes yet — just shapes the code downstream depends on.

**Commit at end:** `Add analyses model and Mongo collection`

---

### Task 2.1: Add nested types — KpiValue, RequirementCheck, Anomaly

**Files:**
- Modify: `test-manager-backend/api/models.py` (append after LogbookEntry block)
- Test: `test-manager-backend/tests/test_analyses.py` (new file)

- [ ] **Step 1: Create the new test file with failing nested-type tests**

Create `test-manager-backend/tests/test_analyses.py`:

```python
"""Tests for the Analysis Pydantic models and CRUD routes."""

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from api.models import (
    Analysis,
    AnalysisCreate,
    AnalysisListQuery,
    Anomaly,
    KpiValue,
    RequirementCheck,
    SaveAnalysisPayload,
)


# --- Nested types --------------------------------------------------------- #


def test_kpi_value_minimal():
    k = KpiValue(name="best_lap", value="1:45.321")
    assert k.unit is None
    assert k.notes is None


def test_kpi_value_full():
    k = KpiValue(name="top_speed", value=213.4, unit="km/h", notes="lap 9 main straight")
    assert k.value == 213.4
    assert k.unit == "km/h"


def test_requirement_check_tri_state_met():
    assert RequirementCheck(requirement="x", met=True).met is True
    assert RequirementCheck(requirement="x", met=False).met is False
    assert RequirementCheck(requirement="x", met=None).met is None
    assert RequirementCheck(requirement="x").met is None  # default


def test_anomaly_severity_literal():
    a = Anomaly(severity="warn", kind="brake_spike", description="hot brake")
    assert a.severity == "warn"
    with pytest.raises(ValidationError):
        Anomaly(severity="critical", kind="x", description="y")  # invalid literal
```

- [ ] **Step 2: Run tests, confirm they fail**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: FAIL — `ImportError: cannot import name 'KpiValue' from 'api.models'` (or similar).

- [ ] **Step 3: Add the three nested types to `api/models.py`**

Append after the LogbookEntry block in `api/models.py`:

```python
# ============================================================================
# Analysis Models
# ============================================================================


class KpiValue(BaseModel):
    """One measurable KPI surfaced by the AI agent."""

    name: str                              # opaque string — e.g. "best_lap"
    value: float | str
    unit: str | None = None
    notes: str | None = None


class RequirementCheck(BaseModel):
    """One requirement extracted from Test.requirements + verdict."""

    requirement: str                       # free text echoing Test.requirements
    met: bool | None = None                # tri-state: true / false / None (undetermined)
    evidence: str | None = None


class Anomaly(BaseModel):
    """One detected event of note (brake spike, off-track, telemetry gap, ...)."""

    severity: Literal["info", "warn", "error"]
    kind: str                              # opaque string — e.g. "brake_spike"
    lap: int | None = None
    time_ms: int | None = None
    description: str
    evidence: str | None = None
```

Confirm `Literal` is imported at top of file (probably already is — search for `from typing import` or `Literal`).

- [ ] **Step 4: Run tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: PASS (the 4 nested-type tests).

---

### Task 2.2: Add Analysis main model

**Files:**
- Modify: `test-manager-backend/api/models.py`
- Modify: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Write failing tests for Analysis defaults and field constraints**

Append to `tests/test_analyses.py`:

```python
# --- Analysis main model -------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_analysis_minimal_defaults():
    a = Analysis(_id="uuid-abc", test_id="TST-1", session_id="2026-01-01T00:00:00Z",
                 status="pending")
    assert a.id == "uuid-abc"
    assert a.schema_version == 1
    assert a.kpis == []
    assert a.requirements_check == []
    assert a.anomalies == []
    assert a.logbook_refs == []
    assert a.summary_md == ""
    assert a.extra == {}
    assert a.tokens_in is None
    assert a.tokens_cache_read is None
    assert a.error is None


def test_analysis_round_trip_with_alias():
    """Pydantic must accept either `id` or `_id` when populate_by_name=True."""
    a = Analysis(_id="uuid-xyz", test_id="t", session_id="s", status="pending")
    dumped = a.model_dump(by_alias=True)
    assert dumped["_id"] == "uuid-xyz"
    again = Analysis(**dumped)
    assert again.id == "uuid-xyz"


def test_analysis_invalid_status_rejected():
    with pytest.raises(ValidationError):
        Analysis(_id="x", test_id="t", session_id="s", status="bogus")  # type: ignore[arg-type]


def test_analysis_invalid_error_kind_rejected():
    with pytest.raises(ValidationError):
        Analysis(_id="x", test_id="t", session_id="s", status="failed",
                 error_kind="bogus")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run tests, confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v -k "analysis"`

Expected: FAIL — `cannot import name 'Analysis'`.

- [ ] **Step 3: Add Analysis main model**

Append to `api/models.py`:

```python
class Analysis(BaseModel):
    """Persisted analysis result. One doc per click of Analyze."""

    id: str = Field(..., alias="_id")              # uuid4 string
    schema_version: int = 1                        # bump on breaking shape changes
    test_id: str
    session_id: str                                # v1 always set
    status: Literal[
        "pending", "running", "fetching",
        "analyzing", "saving", "complete", "failed",
    ]
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)

    # Quix.AI session linkage (for debug)
    quix_session_id: str | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache_create: int | None = None
    tokens_cache_read: int | None = None
    duration_ms: int | None = None

    # Failure info (only set when status="failed")
    error: str | None = None
    error_kind: Literal["timeout", "agent", "validation", "orphan"] | None = None

    # Content — only populated on save_analysis MCP call
    kpis: list[KpiValue] = []
    requirements_check: list[RequirementCheck] = []
    logbook_refs: list[str] = []
    anomalies: list[Anomaly] = []
    summary_md: str = ""                           # required at save time; "" while pending
    extra: dict[str, Any] = {}                     # freeform escape hatch

    model_config = ConfigDict(populate_by_name=True)
```

Confirm `ConfigDict` is imported (`from pydantic import BaseModel, ConfigDict, Field, ...`). If not, add it.

- [ ] **Step 4: Run tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v -k "analysis"`

Expected: PASS.

---

### Task 2.3: Add AnalysisCreate, AnalysisListQuery, SaveAnalysisPayload

**Files:**
- Modify: `test-manager-backend/api/models.py`
- Modify: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Write failing tests for the three request models**

Append to `tests/test_analyses.py`:

```python
# --- Request models ------------------------------------------------------- #


def test_analysis_create_requires_both_ids():
    with pytest.raises(ValidationError):
        AnalysisCreate(test_id="", session_id="s")  # min_length=1 on test_id
    with pytest.raises(ValidationError):
        AnalysisCreate(test_id="t", session_id="")
    ok = AnalysisCreate(test_id="t", session_id="s")
    assert ok.test_id == "t"


def test_save_analysis_payload_requires_summary_md():
    """summary_md is the only required content field — it's the narrative spine."""
    with pytest.raises(ValidationError):
        SaveAnalysisPayload(analysis_id="x", summary_md="")  # min_length=1
    ok = SaveAnalysisPayload(analysis_id="x", summary_md="# ok")
    assert ok.kpis == []                # all other content fields optional, default empty
    assert ok.requirements_check == []
    assert ok.anomalies == []
    assert ok.extra == {}


def test_analysis_list_query_status_literal():
    q = AnalysisListQuery(status="complete")
    assert q.status == "complete"
    with pytest.raises(ValidationError):
        AnalysisListQuery(status="bogus")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run, confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: FAIL — missing imports.

- [ ] **Step 3: Add the three request models**

Append to `api/models.py`:

```python
class AnalysisCreate(BaseModel):
    """Request body for POST /api/v1/analyses."""

    test_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)


class AnalysisListQuery(PaginationParams):
    """Query parameters for GET /api/v1/analyses."""

    test_id: str | None = None
    session_id: str | None = None
    status: Literal["complete", "failed", "in_progress"] | None = None


class SaveAnalysisPayload(BaseModel):
    """MCP write tool input — agent submits this via save_analysis."""

    analysis_id: str
    kpis: list[KpiValue] = []
    requirements_check: list[RequirementCheck] = []
    logbook_refs: list[str] = []
    anomalies: list[Anomaly] = []
    summary_md: str = Field(..., min_length=1)
    extra: dict[str, Any] = {}
```

Confirm `PaginationParams` is imported (it's used by `TestQuery` etc. — should already be in scope).

- [ ] **Step 4: Run, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: All Phase 2 tests pass.

---

### Task 2.4: Add Mongo indices for analyses + orphan sweep

**Files:**
- Modify: `test-manager-backend/api/mongo.py`

- [ ] **Step 1: Add indices in init_mongo helper**

In `api/mongo.py`, alongside the existing `_mongo.tests.create_index(...)` etc. blocks (locate the file's init function — typically called `_init_indices` or inlined in module init), add:

```python
_mongo.analyses.create_index([("test_id", 1), ("session_id", 1), ("created_at", -1)])
_mongo.analyses.create_index([("status", 1), ("updated_at", 1)])  # orphan sweep query
```

- [ ] **Step 2: Smoke-test indices via existing pytest run**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: All green. Index creation is idempotent — testcontainers Mongo accepts repeated creates.

---

### Task 2.5: Run Phase 2 gates + commit

- [ ] **Step 1: Backend gates**

Run: `docker exec test-manager-backend uv run ruff check . && docker exec test-manager-backend uv run ruff format --check . && docker exec test-manager-backend uv run ty check && docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: green.

- [ ] **Step 2: Commit Phase 2**

```bash
git add test-manager-backend/api/models.py \
        test-manager-backend/api/mongo.py \
        test-manager-backend/tests/test_analyses.py

git commit -m "Add analyses model and Mongo collection"
```

Expected: clean commit.

---

**Phase 2 complete.** Schema + indices ready. Continue to Phase 3 (Analyses CRUD routes).


---

# Phase 3 — Analyses CRUD routes

**Goal:** Add `POST /api/v1/analyses`, `GET /api/v1/analyses`, `GET /api/v1/analyses/{id}`. POST creates a `pending` doc, generates uuid4, and (for now) defers the actual runner spawn to a stub — runner wires in Phase 5. Auth via existing Portal `update_permission` / `read_permission`.

**Commit at end:** `Add analyses CRUD routes`

---

### Task 3.1: Create analyses route module with POST endpoint (stub runner)

**Files:**
- Create: `test-manager-backend/api/routes/analyses.py`
- Modify: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Write failing test for POST → 202 + doc inserted**

Append to `tests/test_analyses.py`:

```python
# --- Routes: POST /api/v1/analyses ---------------------------------------- #


def test_post_analysis_creates_pending_doc_and_returns_202(client, headers, mongo, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id

    response = client.post(
        "/api/v1/analyses",
        headers=headers,
        json={"test_id": test_id, "session_id": session_id},
    )
    assert response.status_code == 202
    body = response.json()
    assert "analysis_id" in body

    # Verify doc landed in Mongo with status=pending
    doc = mongo.analyses.find_one({"_id": body["analysis_id"]})
    assert doc is not None
    assert doc["status"] == "pending"
    assert doc["test_id"] == test_id
    assert doc["session_id"] == session_id
    assert doc["summary_md"] == ""
    assert doc["kpis"] == []


def test_post_analysis_rejects_unknown_session_id(client, headers, seeded_test):
    response = client.post(
        "/api/v1/analyses",
        headers=headers,
        json={"test_id": seeded_test.test_id, "session_id": "2099-01-01T00:00:00Z"},
    )
    assert response.status_code == 400


def test_post_analysis_rejects_unknown_test(client, headers):
    response = client.post(
        "/api/v1/analyses",
        headers=headers,
        json={"test_id": "TST-9999", "session_id": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 404
```

- [ ] **Step 2: Run, confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py::test_post_analysis_creates_pending_doc_and_returns_202 -v`

Expected: FAIL — 404 on route or import error.

- [ ] **Step 3: Create the routes module**

Create `test-manager-backend/api/routes/analyses.py`:

```python
"""CRUD routes for AI-generated session analyses."""

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pymongo.database import Database

from ..auth import read_permission, update_permission
from ..models import Analysis, AnalysisCreate
from ..mongo import get_mongo

logger = logging.getLogger(__name__)

router = APIRouter()


def _spawn_runner_stub(analysis_id: str, test_id: str, session_id: str) -> None:
    """Placeholder for the async runner. Real impl lands in Phase 5.

    Keeps the doc in `pending` state — the test confirms this behaviour.
    Phase 5 swaps this for asyncio.create_task(run_analysis(...)).
    """
    logger.info(
        "[analyses] runner spawn DEFERRED (Phase 5): analysis=%s test=%s session=%s",
        analysis_id, test_id, session_id,
    )


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    responses={202: {"content": {"application/json": {"example": {"analysis_id": "..."}}}}},
)
def create_analysis(
    payload: AnalysisCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    test = mongo.tests.find_one({"_id": payload.test_id})
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    known_session_ids = {s["session_id"] for s in test.get("sessions", [])}
    if payload.session_id not in known_session_ids:
        raise HTTPException(
            status_code=400,
            detail=f"session_id '{payload.session_id}' not found on test {payload.test_id}",
        )

    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc)
    doc = Analysis(
        _id=analysis_id,
        test_id=payload.test_id,
        session_id=payload.session_id,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    mongo.analyses.insert_one(doc.model_dump(by_alias=True))
    logger.info("[analyses] POST create %s (test=%s session=%s)", analysis_id,
                payload.test_id, payload.session_id)

    _spawn_runner_stub(analysis_id, payload.test_id, payload.session_id)
    return {"analysis_id": analysis_id}
```

- [ ] **Step 4: Wire the router into `api/app.py`**

In `test-manager-backend/api/app.py`, locate the existing router-include block (look for `app.include_router(...)` calls). Add:

```python
from .routes.analyses import router as analyses_router

# ... alongside existing app.include_router(tests_router, prefix="/api/v1", ...) etc.
app.include_router(analyses_router, prefix="/api/v1", tags=["Analyses"])
```

- [ ] **Step 5: Run tests, confirm pass**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v -k "post_analysis"`

Expected: all 3 POST tests green.

---

### Task 3.2: Add GET /api/v1/analyses/{id} (detail)

**Files:**
- Modify: `test-manager-backend/api/routes/analyses.py`
- Modify: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_analyses.py`:

```python
# --- Routes: GET /api/v1/analyses/{id} ------------------------------------ #


def test_get_analysis_by_id(client, headers, mongo, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id

    created = client.post(
        "/api/v1/analyses",
        headers=headers,
        json={"test_id": test_id, "session_id": session_id},
    ).json()
    analysis_id = created["analysis_id"]

    response = client.get(f"/api/v1/analyses/{analysis_id}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == analysis_id
    assert body["status"] == "pending"


def test_get_analysis_unknown_id_404(client, headers):
    response = client.get("/api/v1/analyses/nonexistent-uuid", headers=headers)
    assert response.status_code == 404
```

- [ ] **Step 2: Run, confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v -k "get_analysis_by_id or get_analysis_unknown"`

Expected: FAIL (404 with `{detail: "Not Found"}` from FastAPI default).

- [ ] **Step 3: Add the GET-by-id route**

Append to `api/routes/analyses.py`:

```python
@router.get("/analyses/{analysis_id}", response_model=Analysis, response_model_by_alias=False)
def get_analysis(
    analysis_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Analysis:
    doc = mongo.analyses.find_one({"_id": analysis_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    logger.debug("[analyses] GET %s", analysis_id)
    return Analysis(**doc)
```

- [ ] **Step 4: Run tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v -k "get_analysis"`

Expected: green.

---

### Task 3.3: Add GET /api/v1/analyses (list with filters + pagination)

**Files:**
- Modify: `test-manager-backend/api/routes/analyses.py`
- Modify: `test-manager-backend/tests/test_analyses.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_analyses.py`:

```python
# --- Routes: GET /api/v1/analyses (list) ---------------------------------- #


def test_list_analyses_filters_by_test_id(client, headers, seeded_test_with_session,
                                          seeded_other_test_with_session):
    """Two tests, one analysis each. Filter by test_id returns only matching one."""
    t1 = seeded_test_with_session.test_id
    s1 = seeded_test_with_session.sessions[0].session_id
    t2 = seeded_other_test_with_session.test_id
    s2 = seeded_other_test_with_session.sessions[0].session_id

    client.post("/api/v1/analyses", headers=headers,
                json={"test_id": t1, "session_id": s1})
    client.post("/api/v1/analyses", headers=headers,
                json={"test_id": t2, "session_id": s2})

    response = client.get(f"/api/v1/analyses?test_id={t1}", headers=headers)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["test_id"] == t1


def test_list_analyses_sorted_desc_by_created_at(client, headers, seeded_test_with_session):
    t = seeded_test_with_session.test_id
    s = seeded_test_with_session.sessions[0].session_id

    first = client.post("/api/v1/analyses", headers=headers,
                       json={"test_id": t, "session_id": s}).json()["analysis_id"]
    second = client.post("/api/v1/analyses", headers=headers,
                        json={"test_id": t, "session_id": s}).json()["analysis_id"]

    response = client.get(f"/api/v1/analyses?test_id={t}", headers=headers)
    items = response.json()["items"]
    assert items[0]["id"] == second
    assert items[1]["id"] == first


def test_list_analyses_pagination(client, headers, seeded_test_with_session):
    t = seeded_test_with_session.test_id
    s = seeded_test_with_session.sessions[0].session_id

    for _ in range(5):
        client.post("/api/v1/analyses", headers=headers,
                    json={"test_id": t, "session_id": s})

    response = client.get(f"/api/v1/analyses?test_id={t}&page=1&page_size=2", headers=headers)
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["page"] == 1
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v -k "list_analyses"`

Expected: FAIL — no list route.

- [ ] **Step 3: Add list route**

Append to `api/routes/analyses.py`:

```python
@router.get("/analyses")
def list_analyses(
    test_id: str | None = None,
    session_id: str | None = None,
    status_filter: str | None = None,            # exposed as ?status=...
    page: int = 1,
    page_size: int = 20,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if test_id is not None:
        query["test_id"] = test_id
    if session_id is not None:
        query["session_id"] = session_id
    if status_filter is not None:
        if status_filter == "in_progress":
            query["status"] = {"$in": ["pending", "running", "fetching", "analyzing", "saving"]}
        else:
            query["status"] = status_filter

    total = mongo.analyses.count_documents(query)
    skip = max(0, (page - 1) * page_size)
    cursor = mongo.analyses.find(query).sort("created_at", -1).skip(skip).limit(page_size)
    items = [Analysis(**doc).model_dump(by_alias=False) for doc in cursor]

    logger.debug("[analyses] GET list (test_id=%s session_id=%s status=%s) -> %d/%d",
                 test_id, session_id, status_filter, len(items), total)
    return {"items": items, "total": total, "page": page, "page_size": page_size}
```

Note the FastAPI param rename: we expose `status_filter` Python param as `?status=` to clients via FastAPI's query alias. Update to:

```python
from fastapi import Query

@router.get("/analyses")
def list_analyses(
    test_id: str | None = None,
    session_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = 1,
    page_size: int = 20,
    ...
```

(Replace the line with the `Query(..., alias="status")` version.)

- [ ] **Step 4: Run tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: all green.

---

### Task 3.4: Frontend — types + API client for analyses

**Files:**
- Create: `test-manager-frontend/types/analysis.ts`
- Create: `test-manager-frontend/lib/api/analyses.ts`

- [ ] **Step 1: Add the TypeScript types**

Create `test-manager-frontend/types/analysis.ts`:

```typescript
export type AnalysisStatus =
  | "pending"
  | "running"
  | "fetching"
  | "analyzing"
  | "saving"
  | "complete"
  | "failed";

export type ErrorKind = "timeout" | "agent" | "validation" | "orphan";

export interface KpiValue {
  name: string;
  value: number | string;
  unit?: string | null;
  notes?: string | null;
}

export interface RequirementCheck {
  requirement: string;
  met?: boolean | null;
  evidence?: string | null;
}

export interface Anomaly {
  severity: "info" | "warn" | "error";
  kind: string;
  lap?: number | null;
  time_ms?: number | null;
  description: string;
  evidence?: string | null;
}

export interface Analysis {
  id: string;
  schema_version: number;
  test_id: string;
  session_id: string;
  status: AnalysisStatus;
  created_at: string;
  updated_at: string;
  quix_session_id?: string | null;
  model?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  tokens_cache_create?: number | null;
  tokens_cache_read?: number | null;
  duration_ms?: number | null;
  error?: string | null;
  error_kind?: ErrorKind | null;
  kpis: KpiValue[];
  requirements_check: RequirementCheck[];
  logbook_refs: string[];
  anomalies: Anomaly[];
  summary_md: string;
  extra: Record<string, unknown>;
}

export interface AnalysisCreateRequest {
  test_id: string;
  session_id: string;
}

export interface AnalysisListResponse {
  items: Analysis[];
  total: number;
  page: number;
  page_size: number;
}
```

- [ ] **Step 2: Add the API client**

Create `test-manager-frontend/lib/api/analyses.ts` matching the existing `lib/api/*` pattern (check `lib/api/logbook.ts` or `lib/api/tests.ts` for the fetch helper convention):

```typescript
import { useAuthFetch } from "@/lib/auth/use-auth-fetch";
import type {
  Analysis,
  AnalysisCreateRequest,
  AnalysisListResponse,
} from "@/types/analysis";

export function useAnalysesApi() {
  const fetch = useAuthFetch();

  return {
    async create(req: AnalysisCreateRequest): Promise<{ analysis_id: string }> {
      const res = await fetch("/api/v1/analyses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(req),
      });
      if (!res.ok) throw new Error(`Failed to create analysis: ${res.status}`);
      return res.json();
    },

    async get(analysisId: string): Promise<Analysis> {
      const res = await fetch(`/api/v1/analyses/${analysisId}`);
      if (!res.ok) throw new Error(`Failed to fetch analysis ${analysisId}: ${res.status}`);
      return res.json();
    },

    async list(opts?: {
      testId?: string;
      sessionId?: string;
      status?: "complete" | "failed" | "in_progress";
      page?: number;
      pageSize?: number;
    }): Promise<AnalysisListResponse> {
      const params = new URLSearchParams();
      if (opts?.testId) params.set("test_id", opts.testId);
      if (opts?.sessionId) params.set("session_id", opts.sessionId);
      if (opts?.status) params.set("status", opts.status);
      if (opts?.page !== undefined) params.set("page", String(opts.page));
      if (opts?.pageSize !== undefined) params.set("page_size", String(opts.pageSize));
      const qs = params.toString() ? `?${params.toString()}` : "";
      const res = await fetch(`/api/v1/analyses${qs}`);
      if (!res.ok) throw new Error(`Failed to list analyses: ${res.status}`);
      return res.json();
    },
  };
}
```

(If `useAuthFetch` is named differently in the existing codebase, match that name. Check `lib/auth/` for the actual helper.)

- [ ] **Step 3: Type-check frontend**

Run: `docker exec test-manager-frontend npm run type-check`

Expected: 0 errors.

---

### Task 3.5: Run Phase 3 gates + commit

- [ ] **Step 1: Backend full gates**

Run: `docker exec test-manager-backend uv run ruff check . && docker exec test-manager-backend uv run ruff format --check . && docker exec test-manager-backend uv run ty check && docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: green.

- [ ] **Step 2: Frontend gates**

Run: `docker exec test-manager-frontend npm run lint && docker exec test-manager-frontend npm run type-check`

Expected: 0 errors.

- [ ] **Step 3: Commit Phase 3**

```bash
git add test-manager-backend/api/routes/analyses.py \
        test-manager-backend/api/app.py \
        test-manager-backend/tests/test_analyses.py \
        test-manager-frontend/types/analysis.ts \
        test-manager-frontend/lib/api/analyses.ts

git commit -m "Add analyses CRUD routes"
```

Expected: clean commit.

---

**Phase 3 complete.** Routes work end-to-end with a stub runner. Continue to Phase 4 (test-manager MCP server).


---

# Phase 4 — Test Manager MCP server

**Goal:** Expose 9 MCP tools (8 read + 1 write) at `/mcp` on test-manager-backend, authenticated by `X-API-Key`. Adopt the quixlab `_instrument_tool` pattern + `_TOOL_TITLES` map. Each tool gets its own test.

**Commit at end:** `Add test-manager MCP server with read tools and save_analysis`

---

### Task 4.1: Set up FastMCP subrouter skeleton + X-API-Key auth + `_instrument_tool` decorator

**Files:**
- Create: `test-manager-backend/api/routes/mcp/__init__.py`
- Create: `test-manager-backend/api/routes/mcp/instrument.py`
- Create: `test-manager-backend/api/routes/mcp/tools.py`
- Create: `test-manager-backend/api/routes/mcp/handlers/__init__.py` (empty)
- Create: `test-manager-backend/tests/test_mcp_server.py`
- Modify: `test-manager-backend/api/app.py`
- Modify: `test-manager-backend/api/settings.py`
- Modify: `test-manager-backend/pyproject.toml`

- [ ] **Step 1: Add `mcp` library dependency**

In `test-manager-backend/pyproject.toml`, add `mcp` to `[project.dependencies]` block:

```toml
dependencies = [
    # ... existing ...
    "mcp>=1.0.0",
]
```

Run: `docker exec test-manager-backend uv sync`

Expected: lockfile updated, dependency installed.

- [ ] **Step 2: Add settings field for MCP API key**

In `test-manager-backend/api/settings.py`, add to the `Settings` class:

```python
testmanager_mcp_api_key: str = ""   # shared secret for /mcp endpoint
```

This reads from env var `TESTMANAGER_MCP_API_KEY` automatically per pydantic-settings convention used in the file.

- [ ] **Step 3: Write a failing auth test**

Create `test-manager-backend/tests/test_mcp_server.py`:

```python
"""Tests for the test-manager MCP server subrouter mounted at /mcp."""

import pytest


@pytest.fixture
def mcp_headers(settings_with_mcp_key):
    """Headers including a valid X-API-Key for /mcp requests."""
    return {"X-API-Key": settings_with_mcp_key.testmanager_mcp_api_key}


def test_mcp_rejects_missing_api_key(client):
    """No X-API-Key header → 401."""
    response = client.post("/mcp/", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code in (401, 403)


def test_mcp_rejects_wrong_api_key(client):
    response = client.post(
        "/mcp/",
        headers={"X-API-Key": "wrong-key"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code in (401, 403)
```

The fixtures `client` and `settings_with_mcp_key` need to be added to `conftest.py`. Add:

```python
# In tests/conftest.py — append to existing file

@pytest.fixture
def settings_with_mcp_key(monkeypatch, settings):
    """Force a known MCP API key for tests."""
    monkeypatch.setenv("TESTMANAGER_MCP_API_KEY", "test-mcp-key-abc123")
    # Reset settings cache if applicable
    settings.testmanager_mcp_api_key = "test-mcp-key-abc123"
    return settings
```

(Adjust per the existing `settings` fixture in `conftest.py` — match the file's conventions.)

- [ ] **Step 4: Run, confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: FAIL — no `/mcp` route mounted yet.

- [ ] **Step 5: Create the `_instrument_tool` decorator**

Create `test-manager-backend/api/routes/mcp/instrument.py` (adapted from `quixlab/src/quixlab/server/mcp/server.py:61-114`):

```python
"""Decorator that wraps every MCP tool callable with structured logging.

Adapted from quixlab/src/quixlab/server/mcp/server.py. Logs at DEBUG by default
per spec §7: INFO would be too chatty (5-10 tool calls per analysis × every analysis).
WARN on exception with class name + duration.

`functools.wraps` preserves `__signature__` so FastMCP's JSON-schema introspection
still sees the original parameter list.
"""

import asyncio
import functools
import inspect
import logging
import time
from typing import Any, Callable


def instrument_tool(name: str, fn: Callable[..., Any], logger: logging.Logger) -> Callable[..., Any]:
    """Wrap an MCP tool callable so each dispatch emits structured log entries.

    DEBUG entry: tool name + sorted kwarg keys (never values — payloads may carry
    sensitive driver names / telemetry rows).
    DEBUG exit: duration + ok.
    WARN on raise with exception class + duration.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.debug("mcp tool: %s called (kwargs=%s)", name, sorted(kwargs.keys()))
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                dur_ms = (time.perf_counter() - start) * 1000
                logger.warning(
                    "mcp tool: %s raised %s after %.1fms — %s",
                    name, type(exc).__name__, dur_ms, exc,
                )
                raise
            dur_ms = (time.perf_counter() - start) * 1000
            logger.debug("mcp tool: %s ok in %.1fms", name, dur_ms)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.debug("mcp tool: %s called (kwargs=%s)", name, sorted(kwargs.keys()))
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            dur_ms = (time.perf_counter() - start) * 1000
            logger.warning(
                "mcp tool: %s raised %s after %.1fms — %s",
                name, type(exc).__name__, dur_ms, exc,
            )
            raise
        dur_ms = (time.perf_counter() - start) * 1000
        logger.debug("mcp tool: %s ok in %.1fms", name, dur_ms)
        return result

    return sync_wrapper
```

- [ ] **Step 6: Create the tool-titles map (initially empty)**

Create `test-manager-backend/api/routes/mcp/tools.py`:

```python
"""Tool registration constants. Tools themselves live in handlers/*.py."""

# Human-readable titles surfaced via MCP `Tool.title` (spec 2025-03-26).
# Quix.AI's .NET backend renders these in chat tool-call cards instead of
# the raw snake_case name. Sentence case matches Quix.AI's built-in style.
TOOL_TITLES: dict[str, str] = {
    "get_test":                          "Get test",
    "get_session":                       "Get session",
    "list_logbook":                      "List logbook entries",
    "get_driver":                        "Get driver",
    "get_device":                        "Get device",
    "get_environment":                   "Get environment",
    "list_sessions_for_test":            "List sessions for test",
    "list_recent_sessions_for_driver":   "List recent sessions for driver",
    "save_analysis":                     "Save analysis",
}
```

- [ ] **Step 7: Create the MCP subrouter `__init__.py`**

Create `test-manager-backend/api/routes/mcp/__init__.py`:

```python
"""MCP server mounted at /mcp on test-manager-backend.

Auth: `X-API-Key` header against `Settings.testmanager_mcp_api_key`.
Tool registration: FastMCP with name slug "test-manager"; Quix.AI bridges
tool names to Claude as `mcp__test-manager__<tool>` (see spec §4.5).
"""

import logging
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from mcp.server.fastmcp import FastMCP
from pymongo.database import Database

from ..auth import _token_preview
from ..mongo import get_mongo
from ..settings import Settings, get_settings
from .instrument import instrument_tool
from .tools import TOOL_TITLES

logger = logging.getLogger(__name__)


def _build_tools(mongo: Database[dict[str, Any]]) -> dict[str, Any]:
    """Returns tool callable dict. Handlers wired in subsequent tasks."""
    # Phase 4.1: empty — handlers added in 4.2-4.5
    return {}


def install(app: FastAPI) -> None:
    """Mount the MCP server at /mcp with API-key auth middleware."""
    settings = get_settings()

    mcp = FastMCP(name="test-manager")

    # Register tools.
    tools = _build_tools(get_mongo().__next__())  # NB: works because get_mongo is a yield-fixture
    for name, fn in tools.items():
        mcp.tool(name=name, title=TOOL_TITLES.get(name))(
            instrument_tool(name, fn, logger)
        )

    sub_app = mcp.streamable_http_app()

    # Pre-mount auth middleware: rejects without X-API-Key.
    @sub_app.middleware("http")
    async def _check_api_key(request: Request, call_next):  # type: ignore[no-untyped-def]
        provided = request.headers.get("X-API-Key", "")
        expected = settings.testmanager_mcp_api_key
        if not expected or provided != expected:
            origin = request.client.host if request.client else "unknown"
            logger.warning(
                "[mcp] wrong X-API-Key from %s (provided=%s)",
                origin, _token_preview(provided),
            )
            raise HTTPException(status_code=401, detail="invalid api key")
        return await call_next(request)

    app.mount("/mcp", sub_app)
    logger.info("[mcp] mounted at /mcp (tools=%d)", len(tools))
```

Notes about the `get_mongo` call: `get_mongo` is a FastAPI dependency generator; we can't call it directly at startup. Fix by passing a Mongo handle through:

Refactor `install` to accept `mongo`:

```python
def install(app: FastAPI, mongo: Database[dict[str, Any]]) -> None:
    settings = get_settings()
    mcp = FastMCP(name="test-manager")
    tools = _build_tools(mongo)
    for name, fn in tools.items():
        mcp.tool(name=name, title=TOOL_TITLES.get(name))(
            instrument_tool(name, fn, logger)
        )
    # ... auth middleware as above ...
    app.mount("/mcp", sub_app)
    logger.info("[mcp] mounted at /mcp (tools=%d)", len(tools))
```

- [ ] **Step 8: Wire into `api/app.py`**

In `test-manager-backend/api/app.py`, after the Mongo init + router includes:

```python
from .routes import mcp as mcp_router

# ... existing init ...

# Mount MCP subrouter (Phase 4)
mcp_router.install(app, mongo=mongo_db)   # use whatever the resolved Mongo handle is named in this file
```

- [ ] **Step 9: Run auth tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: PASS (the two auth-reject tests). MCP route mounted; 401 on bad key.

---

### Task 4.2: Add core read handlers — get_test, get_session, list_logbook

**Files:**
- Create: `test-manager-backend/api/routes/mcp/handlers/core.py`
- Modify: `test-manager-backend/api/routes/mcp/__init__.py` (register handlers)
- Modify: `test-manager-backend/tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests for the three core tools**

Append to `tests/test_mcp_server.py`:

```python
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@pytest.fixture
async def mcp_client(client_url, mcp_headers):
    """Open an MCP streamable-HTTP client session against the mounted /mcp endpoint."""
    async with streamablehttp_client(f"{client_url}/mcp", headers=mcp_headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def test_get_test_tool(mcp_client, seeded_test_with_session):
    result = await mcp_client.call_tool("get_test", {"test_id": seeded_test_with_session.test_id})
    payload = result.content[0].text  # FastMCP returns text content blocks for JSON
    import json
    data = json.loads(payload)
    assert data["test_id"] == seeded_test_with_session.test_id
    assert "driver_name" in data            # resolved name, not just ID
    assert "sessions" in data
    assert len(data["sessions"]) >= 1


async def test_get_test_unknown_id_raises(mcp_client):
    with pytest.raises(Exception, match="not found"):
        await mcp_client.call_tool("get_test", {"test_id": "TST-9999"})


async def test_get_session_tool(mcp_client, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    result = await mcp_client.call_tool("get_session", {"test_id": test_id, "session_id": session_id})
    import json
    data = json.loads(result.content[0].text)
    assert data["session_id"] == session_id


async def test_list_logbook_filter_by_session(mcp_client, client, headers, seeded_test_with_session):
    """Create entries via REST, list via MCP."""
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id

    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "tied", "session_id": session_id})
    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "wide"})

    import json
    # session_id only — wide entry excluded
    result = await mcp_client.call_tool("list_logbook", {
        "test_id": test_id,
        "session_id": session_id,
    })
    items = json.loads(result.content[0].text)
    assert len(items) == 1
    assert items[0]["content"] == "tied"

    # include_test_wide — both included
    result = await mcp_client.call_tool("list_logbook", {
        "test_id": test_id,
        "session_id": session_id,
        "include_test_wide": True,
    })
    items = json.loads(result.content[0].text)
    contents = {i["content"] for i in items}
    assert contents == {"tied", "wide"}
```

Also need `client_url` fixture — add to `conftest.py`:

```python
@pytest.fixture
def client_url(client) -> str:
    """Base URL of the test client. testcontainers fixture exposes this; if pytest's
    TestClient is in use, the URL is `http://testserver` by FastAPI default."""
    return "http://testserver"
```

If the existing client is a `httpx.AsyncClient` against an in-process app, the MCP client setup may differ — check how chat_ui or telemetry-comparison tests handle this and mirror. Otherwise, fall back to **direct callable testing** (calling tool fns straight without the MCP transport layer) — simpler:

```python
# Alternative simpler approach: import + call the tool callables directly,
# bypassing the MCP transport. Add to test_mcp_server.py:

from api.routes.mcp.handlers.core import get_test as get_test_handler


def test_get_test_tool_direct(mongo, seeded_test_with_session):
    result = get_test_handler(mongo, test_id=seeded_test_with_session.test_id)
    assert result["test_id"] == seeded_test_with_session.test_id
    assert "driver_name" in result
```

**Pick one approach for all 9 tool tests**. The "direct callable" approach is much simpler for v1; the streamable-HTTP-client approach exercises the transport too but adds harness complexity. **Recommend direct-callable tests for unit-level coverage + one E2E auth test through the HTTP layer**.

Use this pattern below. Rewrite the above tests to direct-call style:

```python
import json

from api.routes.mcp.handlers.core import (
    get_test as get_test_handler,
    get_session as get_session_handler,
    list_logbook as list_logbook_handler,
)


def test_get_test_returns_resolved_names(mongo, seeded_test_with_session):
    result = get_test_handler(mongo, test_id=seeded_test_with_session.test_id)
    assert result["test_id"] == seeded_test_with_session.test_id
    assert result["driver_name"] is not None
    assert result["pc_device_name"] is not None


def test_get_test_unknown_raises_value_error(mongo):
    with pytest.raises(ValueError, match="not found"):
        get_test_handler(mongo, test_id="TST-9999")


def test_get_session_returns_session_info(mongo, seeded_test_with_session):
    sid = seeded_test_with_session.sessions[0].session_id
    result = get_session_handler(mongo,
                                  test_id=seeded_test_with_session.test_id,
                                  session_id=sid)
    assert result["session_id"] == sid


def test_list_logbook_filter_by_session(mongo, seeded_test_with_session, headers, client):
    test_id = seeded_test_with_session.test_id
    sid = seeded_test_with_session.sessions[0].session_id
    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "tied", "session_id": sid})
    client.post(f"/api/v1/tests/{test_id}/logbook", headers=headers,
                json={"content": "wide"})

    items = list_logbook_handler(mongo, test_id=test_id, session_id=sid)
    assert [i["content"] for i in items] == ["tied"]

    items = list_logbook_handler(mongo, test_id=test_id, session_id=sid, include_test_wide=True)
    assert {i["content"] for i in items} == {"tied", "wide"}
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: FAIL — handlers don't exist.

- [ ] **Step 3: Implement the three core handlers**

Create `test-manager-backend/api/routes/mcp/handlers/core.py`:

```python
"""Core read tools: get_test, get_session, list_logbook."""

from typing import Any

from pymongo.database import Database

from ...models import LogbookEntry
from ...routes.tests import resolve_test_names
from ...models import Test


def get_test(mongo: Database[dict[str, Any]], *, test_id: str) -> dict[str, Any]:
    """Fetch a Test with resolved display names (driver_name, device names, env name)."""
    doc = mongo.tests.find_one({"_id": test_id})
    if not doc:
        raise ValueError(f"Test {test_id} not found")
    test = resolve_test_names(Test(**doc), mongo)
    return test.model_dump(by_alias=False)


def get_session(mongo: Database[dict[str, Any]], *, test_id: str, session_id: str) -> dict[str, Any]:
    """Fetch a single SessionInfo subdoc from a test."""
    doc = mongo.tests.find_one({"_id": test_id})
    if not doc:
        raise ValueError(f"Test {test_id} not found")
    for s in doc.get("sessions", []):
        if s["session_id"] == session_id:
            return s
    raise ValueError(f"session_id {session_id} not on test {test_id}")


def list_logbook(
    mongo: Database[dict[str, Any]],
    *,
    test_id: str,
    session_id: str | None = None,
    include_test_wide: bool = False,
) -> list[dict[str, Any]]:
    """List logbook entries for a test, optionally filtered by session.

    Ordering: ascending by created_at (chronological — agent reads in order events happened).
    """
    if session_id is not None:
        if include_test_wide:
            query: dict[str, Any] = {
                "test_id": test_id,
                "$or": [{"session_id": session_id}, {"session_id": None}],
            }
        else:
            query = {"test_id": test_id, "session_id": session_id}
    else:
        query = {"test_id": test_id}

    cursor = mongo.logbook.find(query).sort("created_at", 1)
    return [LogbookEntry(**doc).model_dump(by_alias=False) for doc in cursor]
```

- [ ] **Step 4: Register handlers in `_build_tools`**

In `test-manager-backend/api/routes/mcp/__init__.py`, update `_build_tools`:

```python
from .handlers.core import get_test, get_session, list_logbook


def _build_tools(mongo: Database[dict[str, Any]]) -> dict[str, Any]:
    return {
        "get_test":     lambda *, test_id: get_test(mongo, test_id=test_id),
        "get_session":  lambda *, test_id, session_id: get_session(mongo, test_id=test_id, session_id=session_id),
        "list_logbook": lambda *, test_id, session_id=None, include_test_wide=False:
            list_logbook(mongo, test_id=test_id, session_id=session_id, include_test_wide=include_test_wide),
    }
```

(Use `functools.partial` if preferred for clarity over lambdas — same effect.)

- [ ] **Step 5: Run tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: PASS for core tests + earlier auth tests.

---

### Task 4.3: Add cross-reference lookup handlers — get_driver, get_device, get_environment

**Files:**
- Create: `test-manager-backend/api/routes/mcp/handlers/lookups.py`
- Modify: `test-manager-backend/api/routes/mcp/__init__.py`
- Modify: `test-manager-backend/tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp_server.py`:

```python
from api.routes.mcp.handlers.lookups import (
    get_driver as get_driver_handler,
    get_device as get_device_handler,
    get_environment as get_environment_handler,
)


def test_get_driver_by_id(mongo, seeded_driver):
    result = get_driver_handler(mongo, id=seeded_driver.driver_id)
    assert result["driver_id"] == seeded_driver.driver_id
    assert result["name"] == seeded_driver.name


def test_get_driver_unknown_raises(mongo):
    with pytest.raises(ValueError, match="not found"):
        get_driver_handler(mongo, id="DRV-9999")


def test_get_device_by_id(mongo, seeded_device):
    result = get_device_handler(mongo, id=seeded_device.device_id)
    assert result["device_id"] == seeded_device.device_id


def test_get_environment_by_id(mongo, seeded_environment):
    result = get_environment_handler(mongo, id=seeded_environment.environment_id)
    assert result["environment_id"] == seeded_environment.environment_id
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v -k "get_driver or get_device or get_environment"`

Expected: FAIL — handlers don't exist.

- [ ] **Step 3: Implement lookup handlers**

Create `test-manager-backend/api/routes/mcp/handlers/lookups.py`:

```python
"""Cross-reference lookups: drivers, devices, environments."""

from typing import Any

from pymongo.database import Database

from ...models import Device, Driver, Environment


def get_driver(mongo: Database[dict[str, Any]], *, id: str) -> dict[str, Any]:
    doc = mongo.drivers.find_one({"_id": id})
    if not doc:
        raise ValueError(f"Driver {id} not found")
    return Driver(**doc).model_dump(by_alias=False)


def get_device(mongo: Database[dict[str, Any]], *, id: str) -> dict[str, Any]:
    doc = mongo.devices.find_one({"_id": id})
    if not doc:
        raise ValueError(f"Device {id} not found")
    return Device(**doc).model_dump(by_alias=False)


def get_environment(mongo: Database[dict[str, Any]], *, id: str) -> dict[str, Any]:
    doc = mongo.environments.find_one({"_id": id})
    if not doc:
        raise ValueError(f"Environment {id} not found")
    return Environment(**doc).model_dump(by_alias=False)
```

- [ ] **Step 4: Register in `_build_tools`**

Update `api/routes/mcp/__init__.py`:

```python
from .handlers.lookups import get_driver, get_device, get_environment


def _build_tools(mongo: Database[dict[str, Any]]) -> dict[str, Any]:
    return {
        # core (existing)
        "get_test":     lambda *, test_id: get_test(mongo, test_id=test_id),
        "get_session":  lambda *, test_id, session_id: get_session(mongo, test_id=test_id, session_id=session_id),
        "list_logbook": lambda *, test_id, session_id=None, include_test_wide=False:
            list_logbook(mongo, test_id=test_id, session_id=session_id, include_test_wide=include_test_wide),
        # lookups (new)
        "get_driver":      lambda *, id: get_driver(mongo, id=id),
        "get_device":      lambda *, id: get_device(mongo, id=id),
        "get_environment": lambda *, id: get_environment(mongo, id=id),
    }
```

- [ ] **Step 5: Run tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: green.

---

### Task 4.4: Add history handlers — list_sessions_for_test, list_recent_sessions_for_driver

**Files:**
- Create: `test-manager-backend/api/routes/mcp/handlers/history.py`
- Modify: `test-manager-backend/api/routes/mcp/__init__.py`
- Modify: `test-manager-backend/tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp_server.py`:

```python
from api.routes.mcp.handlers.history import (
    list_sessions_for_test as list_sessions_handler,
    list_recent_sessions_for_driver as list_recent_handler,
)


def test_list_sessions_for_test_sorted_desc(mongo, seeded_test_with_two_sessions):
    """Sessions sorted desc by session_id (ISO timestamp lexicographic == chronological)."""
    test_id = seeded_test_with_two_sessions.test_id
    expected_first = sorted(
        [s.session_id for s in seeded_test_with_two_sessions.sessions],
        reverse=True,
    )[0]
    sessions = list_sessions_handler(mongo, test_id=test_id)
    assert sessions[0]["session_id"] == expected_first


def test_list_recent_sessions_for_driver_limits_default_5(mongo, seeded_driver_with_many_tests):
    """Pulls sessions across multiple tests for one driver, capped at limit."""
    driver_id = seeded_driver_with_many_tests.driver_id
    result = list_recent_handler(mongo, driver_id=driver_id, limit=5)
    assert len(result) <= 5
    # Most recent first
    if len(result) > 1:
        assert result[0]["session_id"] >= result[1]["session_id"]
```

(Fixtures `seeded_test_with_two_sessions`, `seeded_driver_with_many_tests` need to be added to `conftest.py` — mirror existing seed patterns.)

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v -k "list_sessions or list_recent"`

Expected: FAIL.

- [ ] **Step 3: Implement handlers**

Create `test-manager-backend/api/routes/mcp/handlers/history.py`:

```python
"""Historical session lookups for baseline / cross-session comparison."""

from typing import Any

from pymongo.database import Database


def list_sessions_for_test(
    mongo: Database[dict[str, Any]], *, test_id: str
) -> list[dict[str, Any]]:
    """All sessions on a test, sorted descending by session_id (latest first)."""
    doc = mongo.tests.find_one({"_id": test_id})
    if not doc:
        raise ValueError(f"Test {test_id} not found")
    sessions = list(doc.get("sessions", []))
    return sorted(sessions, key=lambda s: s["session_id"], reverse=True)


def list_recent_sessions_for_driver(
    mongo: Database[dict[str, Any]], *, driver_id: str, limit: int = 5,
) -> list[dict[str, Any]]:
    """Flat list of recent sessions across all tests for a driver.

    Each item: {test_id, session_id, track, car_model, created_at}.
    Capped at min(limit, 20).
    """
    limit = max(1, min(limit, 20))

    pipeline: list[dict[str, Any]] = [
        {"$match": {"driver": driver_id}},
        {"$unwind": "$sessions"},
        {"$project": {
            "test_id": "$_id",
            "session_id": "$sessions.session_id",
            "track": "$sessions.track",
            "car_model": "$sessions.car_model",
            "created_at": "$created_at",
        }},
        {"$sort": {"session_id": -1}},
        {"$limit": limit},
    ]
    return list(mongo.tests.aggregate(pipeline))
```

- [ ] **Step 4: Register handlers**

In `api/routes/mcp/__init__.py`:

```python
from .handlers.history import list_sessions_for_test, list_recent_sessions_for_driver


def _build_tools(mongo):
    return {
        # ... existing ...
        "list_sessions_for_test":
            lambda *, test_id: list_sessions_for_test(mongo, test_id=test_id),
        "list_recent_sessions_for_driver":
            lambda *, driver_id, limit=5: list_recent_sessions_for_driver(
                mongo, driver_id=driver_id, limit=limit),
    }
```

- [ ] **Step 5: Run tests**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: green.

---

### Task 4.5: Add write handler — save_analysis

**Files:**
- Create: `test-manager-backend/api/routes/mcp/handlers/write.py`
- Modify: `test-manager-backend/api/routes/mcp/__init__.py`
- Modify: `test-manager-backend/tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp_server.py`:

```python
from datetime import datetime, timezone
from api.routes.mcp.handlers.write import save_analysis as save_analysis_handler


def _make_pending_doc(mongo, test_id, session_id, status="running"):
    """Helper: insert a doc directly into the analyses collection ready for save."""
    from uuid import uuid4
    aid = str(uuid4())
    mongo.analyses.insert_one({
        "_id": aid,
        "schema_version": 1,
        "test_id": test_id,
        "session_id": session_id,
        "status": status,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "kpis": [], "requirements_check": [], "logbook_refs": [],
        "anomalies": [], "summary_md": "", "extra": {},
    })
    return aid


def test_save_analysis_writes_payload(mongo, seeded_test_with_session):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    aid = _make_pending_doc(mongo, test_id, session_id)

    result = save_analysis_handler(mongo,
        analysis_id=aid,
        summary_md="## Pace\n\nDriver did fine.",
        kpis=[{"name": "best_lap", "value": "1:45.321"}],
        requirements_check=[],
        logbook_refs=[],
        anomalies=[],
        extra={"weather": "20C dry"},
    )
    assert result == {"ok": True, "analysis_id": aid}

    doc = mongo.analyses.find_one({"_id": aid})
    assert doc["status"] == "complete"
    assert doc["summary_md"].startswith("## Pace")
    assert doc["kpis"] == [{"name": "best_lap", "value": "1:45.321", "unit": None, "notes": None}]
    assert doc["extra"] == {"weather": "20C dry"}


def test_save_analysis_unknown_id_raises(mongo):
    with pytest.raises(ValueError, match="not found"):
        save_analysis_handler(mongo, analysis_id="nonexistent", summary_md="x")


def test_save_analysis_double_call_rejected(mongo, seeded_test_with_session):
    aid = _make_pending_doc(mongo,
                            seeded_test_with_session.test_id,
                            seeded_test_with_session.sessions[0].session_id)
    save_analysis_handler(mongo, analysis_id=aid, summary_md="first")
    with pytest.raises(ValueError, match="already complete"):
        save_analysis_handler(mongo, analysis_id=aid, summary_md="second")


def test_save_analysis_invalid_payload_raises(mongo, seeded_test_with_session):
    aid = _make_pending_doc(mongo,
                            seeded_test_with_session.test_id,
                            seeded_test_with_session.sessions[0].session_id)
    # summary_md is required, min_length=1
    with pytest.raises(Exception):  # pydantic ValidationError
        save_analysis_handler(mongo, analysis_id=aid, summary_md="")
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v -k "save_analysis"`

Expected: FAIL — `cannot import name 'save_analysis'`.

- [ ] **Step 3: Implement save_analysis**

Create `test-manager-backend/api/routes/mcp/handlers/write.py`:

```python
"""Write tool: save_analysis (called by the agent at end of run)."""

import logging
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from ...models import SaveAnalysisPayload

logger = logging.getLogger(__name__)


_TERMINAL_STATUSES = {"complete", "failed"}


def save_analysis(
    mongo: Database[dict[str, Any]],
    *,
    analysis_id: str,
    summary_md: str,
    kpis: list[dict[str, Any]] | None = None,
    requirements_check: list[dict[str, Any]] | None = None,
    logbook_refs: list[str] | None = None,
    anomalies: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the agent's final analysis payload.

    Raises:
      - ValueError if analysis_id doesn't exist (-> MCP 404 equivalent)
      - ValueError if already complete (-> MCP 409 equivalent)
      - Pydantic ValidationError on bad payload (-> MCP 422 equivalent)
    """
    payload = SaveAnalysisPayload(
        analysis_id=analysis_id,
        summary_md=summary_md,
        kpis=kpis or [],
        requirements_check=requirements_check or [],
        logbook_refs=logbook_refs or [],
        anomalies=anomalies or [],
        extra=extra or {},
    )

    doc = mongo.analyses.find_one({"_id": analysis_id})
    if not doc:
        raise ValueError(f"Analysis {analysis_id} not found")
    if doc["status"] in _TERMINAL_STATUSES:
        raise ValueError(
            f"Analysis {analysis_id} already complete (status={doc['status']})"
        )

    update = {
        "kpis": [k.model_dump() for k in payload.kpis],
        "requirements_check": [r.model_dump() for r in payload.requirements_check],
        "logbook_refs": payload.logbook_refs,
        "anomalies": [a.model_dump() for a in payload.anomalies],
        "summary_md": payload.summary_md,
        "extra": payload.extra,
        "status": "complete",
        "updated_at": datetime.now(timezone.utc),
    }
    mongo.analyses.update_one({"_id": analysis_id}, {"$set": update})

    logger.info(
        "[mcp] save_analysis %s — kpis=%d reqs=%d anomalies=%d summary_md_len=%d",
        analysis_id,
        len(payload.kpis),
        len(payload.requirements_check),
        len(payload.anomalies),
        len(payload.summary_md),
    )
    return {"ok": True, "analysis_id": analysis_id}
```

Note the log line follows the §7 redaction rule: counts + length only, never contents.

- [ ] **Step 4: Register in `_build_tools`**

```python
from .handlers.write import save_analysis


def _build_tools(mongo):
    return {
        # ... existing ...
        "save_analysis":
            lambda *, analysis_id, summary_md, kpis=None, requirements_check=None,
                   logbook_refs=None, anomalies=None, extra=None:
                save_analysis(mongo,
                              analysis_id=analysis_id, summary_md=summary_md,
                              kpis=kpis, requirements_check=requirements_check,
                              logbook_refs=logbook_refs, anomalies=anomalies,
                              extra=extra),
    }
```

- [ ] **Step 5: Run tests**

Run: `docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: all 9 tools + auth tests green.

---

### Task 4.6: Run Phase 4 gates + commit

- [ ] **Step 1: Backend gates**

Run: `docker exec test-manager-backend uv run ruff check . && docker exec test-manager-backend uv run ruff format --check . && docker exec test-manager-backend uv run ty check && docker exec test-manager-backend uv run pytest tests/test_mcp_server.py -v`

Expected: all green.

- [ ] **Step 2: Commit Phase 4**

```bash
git add test-manager-backend/api/routes/mcp/ \
        test-manager-backend/api/app.py \
        test-manager-backend/api/settings.py \
        test-manager-backend/tests/test_mcp_server.py \
        test-manager-backend/tests/conftest.py \
        test-manager-backend/pyproject.toml \
        test-manager-backend/uv.lock

git commit -m "Add test-manager MCP server with read tools and save_analysis"
```

Expected: clean commit.

---

**Phase 4 complete.** MCP server exposes all 9 tools + auth. Continue to Phase 5 (analysis runner).


---

# Phase 5 — Analysis runner (asyncio + Quix.AI SSE consumer)

**Goal:** Implement the asyncio task that holds the Quix.AI SSE for one analysis, transitions status as events arrive, enforces 5-min timeout, and cleans up orphans on backend restart. Wire into POST handler in place of the Phase 3 stub.

**Commit at end:** `Add analysis runner with Quix.AI SSE consumer`

---

### Task 5.1: Add respx dev dependency for Quix.AI HTTP mocking

**Files:**
- Modify: `test-manager-backend/pyproject.toml`

- [ ] **Step 1: Add respx to dev dependencies**

In `test-manager-backend/pyproject.toml`, find `[tool.uv.dev-dependencies]` (or `[project.optional-dependencies.dev]` — match existing pattern) and add:

```toml
"respx>=0.21",
```

Run: `docker exec test-manager-backend uv sync`

Expected: respx installed.

---

### Task 5.2: Create analysis_runner module with happy-path SSE consumer

**Files:**
- Create: `test-manager-backend/api/analysis_runner.py`
- Create: `test-manager-backend/tests/test_analysis_runner.py`

- [ ] **Step 1: Write the failing happy-path test**

Create `test-manager-backend/tests/test_analysis_runner.py`:

```python
"""Tests for the analysis runner — asyncio task holding Quix.AI SSE."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
import respx

from api.analysis_runner import run_analysis, cleanup_orphans


PORTAL = "https://portal-api.platform.quix.io"


def _insert_pending(mongo, test_id, session_id):
    aid = str(uuid4())
    mongo.analyses.insert_one({
        "_id": aid, "schema_version": 1, "test_id": test_id, "session_id": session_id,
        "status": "pending", "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
        "kpis": [], "requirements_check": [], "logbook_refs": [],
        "anomalies": [], "summary_md": "", "extra": {},
    })
    return aid


def _sse(events: list[dict]) -> str:
    """Encode a list of event dicts as SSE wire format (newline-delimited JSON with `data:` prefix)."""
    out = []
    for e in events:
        out.append(f"data: {json.dumps(e)}\n\n")
    return "".join(out)


@pytest.fixture
def mock_quix_ai(monkeypatch):
    """Force the runner to use the canonical PORTAL URL in tests."""
    monkeypatch.setenv("Quix__Portal__Api", PORTAL)
    monkeypatch.setenv("Quix__Workspace__Id", "ws-test")
    monkeypatch.setenv("QUIX_AI_POST_RACE_AGENT_ID", "agent-test")
    yield


@respx.mock
async def test_runner_happy_path_marks_complete(mongo, seeded_test_with_session, mock_quix_ai):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    aid = _insert_pending(mongo, test_id, session_id)

    # Mock session-create endpoint
    respx.post(f"{PORTAL}/ai/api/sessions").mock(
        return_value=httpx.Response(200, json={"id": "qsess-1"}),
    )
    # Mock message-stream endpoint with a happy-path SSE sequence:
    # tool_call_start (read) -> tool_result -> tool_call_start (save_analysis) -> save tool_result -> usage -> end
    sse_body = _sse([
        {"type": "tool_call_start", "toolName": "mcp__test-manager__get_test", "toolCallId": "tc1"},
        {"type": "tool_result", "toolCallId": "tc1", "isError": False},
        {"type": "tool_call_start", "toolName": "mcp__test-manager__save_analysis", "toolCallId": "tc2"},
        {"type": "tool_result", "toolCallId": "tc2", "isError": False},
        {"type": "usage", "inputTokens": 4218, "outputTokens": 1132,
         "cacheCreationInputTokens": 100, "cacheReadInputTokens": 2000,
         "model": "claude-opus-4-7"},
    ])
    respx.post(f"{PORTAL}/ai/api/sessions/qsess-1/messages").mock(
        return_value=httpx.Response(200, content=sse_body.encode(),
                                    headers={"content-type": "text/event-stream"}),
    )

    # Simulate the agent calling save_analysis via MCP (happens out-of-band of the SSE mock).
    # In a real run this would flip status to "complete". For the test we manually mark it.
    # Alternatively, the runner only relies on the SSE stream to detect save_analysis tool_call_start
    # and end transitions, then re-reads the doc and updates `model`/`tokens_*`/`duration_ms` from the
    # usage event WITHOUT overwriting status (MCP write side owns that).
    # So before running, simulate save_analysis having been called:
    mongo.analyses.update_one(
        {"_id": aid},
        {"$set": {"status": "complete", "summary_md": "ok"}},
    )

    await run_analysis(mongo, analysis_id=aid, test_id=test_id, session_id=session_id)

    doc = mongo.analyses.find_one({"_id": aid})
    assert doc["status"] == "complete"
    assert doc["quix_session_id"] == "qsess-1"
    assert doc["model"] == "claude-opus-4-7"
    assert doc["tokens_in"] == 4218
    assert doc["tokens_out"] == 1132
    assert doc["tokens_cache_create"] == 100
    assert doc["tokens_cache_read"] == 2000
    assert doc["duration_ms"] is not None
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-backend uv run pytest tests/test_analysis_runner.py -v -k "happy_path"`

Expected: FAIL — `cannot import name 'run_analysis' from 'api.analysis_runner'`.

- [ ] **Step 3: Implement the runner**

Create `test-manager-backend/api/analysis_runner.py`:

```python
"""Asyncio task that holds a Quix.AI SSE session for one analysis run.

Per spec §3 + §5:
  1. Open session via POST /ai/api/sessions
  2. Send seed message with workspaceId context
  3. Read events silently from the response stream
  4. Update analysis.status as we see tool_call_starts (fetching/analyzing/saving)
  5. Persist model + token counts + duration on usage event
  6. Hold connection for the full duration of the run; 5-min hard timeout via wait_for
  7. On any unexpected exit, mark failed with appropriate error_kind
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from pymongo.database import Database

logger = logging.getLogger(__name__)


HARD_TIMEOUT_SECONDS = 300        # 5 minutes
ORPHAN_THRESHOLD = timedelta(minutes=10)
NON_TERMINAL = {"pending", "running", "fetching", "analyzing", "saving"}


def _portal() -> str:
    return os.environ["Quix__Portal__Api"].rstrip("/")


def _seed_message(analysis_id: str, test_id: str, session_id: str) -> dict[str, Any]:
    return {
        "message": (
            "Analyze the racing session below.\n\n"
            f"analysis_id: {analysis_id}\n"
            f"test_id:     {test_id}\n"
            f"session_id:  {session_id}\n\n"
            "Workspace context: AC telemetry, lake table = ac_telemetry.\n\n"
            f'Call save_analysis(analysis_id="{analysis_id}", payload={{...}}) exactly once when done.'
        ),
        "context": {"workspaceId": os.environ["Quix__Workspace__Id"]},
    }


def _classify_status_from_tool_name(tool_name: str | None) -> str | None:
    if not tool_name:
        return None
    if tool_name.startswith("mcp__test-manager__save_analysis"):
        return "saving"
    if tool_name.startswith("mcp__quixlake__") or tool_name == "delegate_task":
        return "analyzing"
    if tool_name.startswith("mcp__test-manager__"):
        return "fetching"
    return None


async def _read_sse_events(response: httpx.Response):
    """Yield parsed SSE event dicts from an `httpx.Response` opened via client.stream()."""
    async for line in response.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        raw = line[len("data:"):].strip()
        if raw == "[DONE]":
            continue
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("[runner] skipping non-JSON SSE line: %r", raw)
            continue


def _set_status(mongo: Database[dict[str, Any]], analysis_id: str, **fields: Any) -> None:
    fields["updated_at"] = datetime.now(timezone.utc)
    mongo.analyses.update_one({"_id": analysis_id}, {"$set": fields})


async def _run_inner(
    mongo: Database[dict[str, Any]],
    *,
    analysis_id: str,
    test_id: str,
    session_id: str,
) -> None:
    portal = _portal()
    agent_id = os.environ["QUIX_AI_POST_RACE_AGENT_ID"]

    started_wall = time.perf_counter()

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None)) as client:
        # 1. Open session
        resp = await client.post(
            f"{portal}/ai/api/sessions",
            json={"agentConfigurationId": agent_id},
        )
        resp.raise_for_status()
        qsess = resp.json().get("id") or resp.json().get("sessionId")
        if not qsess:
            raise RuntimeError("Quix.AI session create response missing id")

        _set_status(mongo, analysis_id, status="running", quix_session_id=qsess)
        logger.info("[runner] analysis %s started qsess=%s", analysis_id, qsess)

        # 2. Send seed + read SSE
        url = f"{portal}/ai/api/sessions/{qsess}/messages"
        async with client.stream("POST", url, json=_seed_message(analysis_id, test_id, session_id)) as stream:
            stream.raise_for_status()
            async for evt in _read_sse_events(stream):
                etype = evt.get("type")
                if etype == "tool_call_start":
                    new_status = _classify_status_from_tool_name(evt.get("toolName"))
                    if new_status:
                        _set_status(mongo, analysis_id, status=new_status)
                elif etype == "usage":
                    _set_status(mongo, analysis_id,
                                model=evt.get("model"),
                                tokens_in=evt.get("inputTokens"),
                                tokens_out=evt.get("outputTokens"),
                                tokens_cache_create=evt.get("cacheCreationInputTokens"),
                                tokens_cache_read=evt.get("cacheReadInputTokens"))

    # 3. Stream ended. If MCP save_analysis hasn't already flipped status to complete,
    #    something went wrong — mark failed.
    doc = mongo.analyses.find_one({"_id": analysis_id})
    duration_ms = int((time.perf_counter() - started_wall) * 1000)
    if doc and doc["status"] != "complete":
        _set_status(mongo, analysis_id, status="failed",
                    error_kind="agent",
                    error="agent did not call save_analysis before stream end",
                    duration_ms=duration_ms)
        logger.warning("[runner] analysis %s failed — agent no-save (duration=%dms)",
                       analysis_id, duration_ms)
    else:
        _set_status(mongo, analysis_id, duration_ms=duration_ms)
        logger.info("[runner] analysis %s completed in %dms", analysis_id, duration_ms)


async def run_analysis(
    mongo: Database[dict[str, Any]],
    *,
    analysis_id: str,
    test_id: str,
    session_id: str,
) -> None:
    """Public entry point. Wraps _run_inner with the 5-min hard timeout + failure handling."""
    try:
        await asyncio.wait_for(
            _run_inner(mongo, analysis_id=analysis_id, test_id=test_id, session_id=session_id),
            timeout=HARD_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        _set_status(mongo, analysis_id, status="failed",
                    error_kind="timeout",
                    error=f"agent exceeded {HARD_TIMEOUT_SECONDS}s budget")
        logger.warning("[runner] analysis %s failed — timeout", analysis_id)
    except Exception as exc:
        _set_status(mongo, analysis_id, status="failed",
                    error_kind="agent",
                    error=f"{type(exc).__name__}: {exc}")
        logger.error("[runner] analysis %s failed — %s: %s", analysis_id, type(exc).__name__, exc)


def cleanup_orphans(mongo: Database[dict[str, Any]]) -> int:
    """On backend startup, mark stuck non-terminal docs as failed with error_kind='orphan'.

    Returns the number of docs marked.
    """
    cutoff = datetime.now(timezone.utc) - ORPHAN_THRESHOLD
    result = mongo.analyses.update_many(
        {"status": {"$in": list(NON_TERMINAL)}, "updated_at": {"$lt": cutoff}},
        {"$set": {
            "status": "failed",
            "error_kind": "orphan",
            "error": "Backend restarted while analysis in progress",
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    if result.modified_count:
        logger.warning("[runner] orphan sweep marked %d analyses failed", result.modified_count)
    return result.modified_count
```

- [ ] **Step 4: Run happy-path test, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_analysis_runner.py::test_runner_happy_path_marks_complete -v`

Expected: PASS.

---

### Task 5.3: Add timeout, SSE-drop, no-save-call, and orphan tests

**Files:**
- Modify: `test-manager-backend/tests/test_analysis_runner.py`

- [ ] **Step 1: Add the 4 failure-mode tests**

Append to `tests/test_analysis_runner.py`:

```python
import api.analysis_runner as runner_mod


@respx.mock
async def test_runner_no_save_marks_failed(mongo, seeded_test_with_session, mock_quix_ai):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    aid = _insert_pending(mongo, test_id, session_id)

    respx.post(f"{PORTAL}/ai/api/sessions").mock(
        return_value=httpx.Response(200, json={"id": "qsess-2"}))

    # SSE ends without save_analysis tool_call_start
    sse_body = _sse([
        {"type": "tool_call_start", "toolName": "mcp__test-manager__get_test", "toolCallId": "tc1"},
        {"type": "tool_result", "toolCallId": "tc1", "isError": False},
        {"type": "usage", "inputTokens": 100, "outputTokens": 50},
    ])
    respx.post(f"{PORTAL}/ai/api/sessions/qsess-2/messages").mock(
        return_value=httpx.Response(200, content=sse_body.encode(),
                                    headers={"content-type": "text/event-stream"}))

    await run_analysis(mongo, analysis_id=aid, test_id=test_id, session_id=session_id)

    doc = mongo.analyses.find_one({"_id": aid})
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "agent"


@respx.mock
async def test_runner_sse_drop_marks_failed(mongo, seeded_test_with_session, mock_quix_ai):
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    aid = _insert_pending(mongo, test_id, session_id)

    respx.post(f"{PORTAL}/ai/api/sessions").mock(
        return_value=httpx.Response(200, json={"id": "qsess-3"}))
    respx.post(f"{PORTAL}/ai/api/sessions/qsess-3/messages").mock(
        side_effect=httpx.ReadError("connection reset"))

    await run_analysis(mongo, analysis_id=aid, test_id=test_id, session_id=session_id)

    doc = mongo.analyses.find_one({"_id": aid})
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "agent"


async def test_runner_timeout_marks_failed(mongo, seeded_test_with_session, mock_quix_ai, monkeypatch):
    """Force a fast timeout by patching HARD_TIMEOUT_SECONDS to 0.1, then have the mock hang."""
    monkeypatch.setattr(runner_mod, "HARD_TIMEOUT_SECONDS", 0.1)

    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    aid = _insert_pending(mongo, test_id, session_id)

    async def _hang(*_args, **_kwargs):
        await asyncio.sleep(10)
        return httpx.Response(200, json={"id": "qsess-4"})

    with respx.mock() as mock:
        mock.post(f"{PORTAL}/ai/api/sessions").mock(side_effect=_hang)
        await run_analysis(mongo, analysis_id=aid, test_id=test_id, session_id=session_id)

    doc = mongo.analyses.find_one({"_id": aid})
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "timeout"


def test_cleanup_orphans_marks_stuck_pending_failed(mongo, seeded_test_with_session):
    """Insert a stale pending doc with updated_at = 30min ago, run cleanup, verify it's marked."""
    test_id = seeded_test_with_session.test_id
    session_id = seeded_test_with_session.sessions[0].session_id
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=30)

    aid = str(uuid4())
    mongo.analyses.insert_one({
        "_id": aid, "schema_version": 1, "test_id": test_id, "session_id": session_id,
        "status": "running", "created_at": stale_at, "updated_at": stale_at,
        "kpis": [], "requirements_check": [], "logbook_refs": [],
        "anomalies": [], "summary_md": "", "extra": {},
    })

    n = cleanup_orphans(mongo)
    assert n == 1

    doc = mongo.analyses.find_one({"_id": aid})
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "orphan"
```

- [ ] **Step 2: Run all runner tests, confirm green**

Run: `docker exec test-manager-backend uv run pytest tests/test_analysis_runner.py -v`

Expected: all 5 tests pass (happy + no-save + sse-drop + timeout + orphan).

---

### Task 5.4: Wire runner into POST /api/v1/analyses (replace Phase 3 stub)

**Files:**
- Modify: `test-manager-backend/api/routes/analyses.py`
- Modify: `test-manager-backend/api/app.py`

- [ ] **Step 1: Replace `_spawn_runner_stub` with the real runner**

In `api/routes/analyses.py`, remove `_spawn_runner_stub` and update `create_analysis`:

```python
import asyncio

from ..analysis_runner import run_analysis


@router.post("/analyses", status_code=status.HTTP_202_ACCEPTED, ...)
def create_analysis(
    payload: AnalysisCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    # ... validation as before, generate analysis_id, insert doc ...

    asyncio.create_task(
        run_analysis(
            mongo,
            analysis_id=analysis_id,
            test_id=payload.test_id,
            session_id=payload.session_id,
        )
    )
    return {"analysis_id": analysis_id}
```

- [ ] **Step 2: Wire orphan-sweep into app startup**

In `api/app.py`, add a startup event handler that calls `cleanup_orphans`:

```python
from .analysis_runner import cleanup_orphans

# ... existing app + mongo setup ...

@app.on_event("startup")
def _runner_orphan_sweep() -> None:
    cleanup_orphans(mongo_db)
```

- [ ] **Step 3: Verify POST integration didn't break Phase 3 tests**

Run: `docker exec test-manager-backend uv run pytest tests/test_analyses.py -v`

Expected: all green. (The POST tests should still pass — they only check the doc lands in pending state immediately after POST, which is still true; the spawn is fire-and-forget asyncio that doesn't block the response.)

If a test fails due to the runner actually trying to call Quix.AI (which it can't in test env), wrap the test client setup with `respx.mock()` or ensure env vars cause `Quix__Portal__Api` to be empty so the runner errors out gracefully into status=failed. Adjust by gating in `create_analysis`:

```python
import os

if os.getenv("Quix__Portal__Api"):
    asyncio.create_task(run_analysis(...))
else:
    logger.warning("[analyses] runner not started — Quix__Portal__Api unset (test or misconfig)")
```

This keeps tests green without changing the contract — production deployments always have the env var.

---

### Task 5.5: Run Phase 5 gates + commit

- [ ] **Step 1: Full backend gates**

Run: `docker exec test-manager-backend uv run ruff check . && docker exec test-manager-backend uv run ruff format --check . && docker exec test-manager-backend uv run ty check && docker exec test-manager-backend uv run pytest -v`

Expected: green (all suites — analyses + mcp_server + analysis_runner + logbook + existing).

- [ ] **Step 2: Commit Phase 5**

```bash
git add test-manager-backend/api/analysis_runner.py \
        test-manager-backend/api/routes/analyses.py \
        test-manager-backend/api/app.py \
        test-manager-backend/tests/test_analysis_runner.py \
        test-manager-backend/pyproject.toml \
        test-manager-backend/uv.lock

git commit -m "Add analysis runner with Quix.AI SSE consumer"
```

Expected: clean.

---

**Phase 5 complete.** Backend end-to-end. Continue to Phase 6 (frontend AI Summary sub-tab).


---

# Phase 6 — Frontend AI Summary sub-tab + vitest setup + deep-link buttons

**Goal:** Build the new "AI Summary" sub-tab under `/analysis`, including the test/session picker, history selector, analysis card (KPI grid + reqs pills + anomalies + Markdown), Analyze button with polling. Drop unused stub sub-tabs (Per-Corner, Live, Single Run, Notebook). Add deep-link AI Summary button to test detail. Introduce vitest for the first time in this repo.

**Commit at end:** `Add AI Summary sub-tab and analyses frontend`

---

### Task 6.1: Set up vitest

**Files:**
- Modify: `test-manager-frontend/package.json`
- Create: `test-manager-frontend/vitest.config.ts`
- Create: `test-manager-frontend/vitest.setup.ts`

- [ ] **Step 1: Add vitest deps and scripts**

In `test-manager-frontend/package.json`, add to `devDependencies` (matching versions from `telemetry-comparison/package.json` for consistency):

```json
"devDependencies": {
  "@testing-library/jest-dom": "^6.4.0",
  "@testing-library/react": "^16.0.0",
  "@testing-library/user-event": "^14.5.0",
  "@vitejs/plugin-react": "^4.3.0",
  "jsdom": "^25.0.0",
  "vitest": "^2.0.0"
}
```

And add scripts:

```json
"scripts": {
  ...existing...,
  "test": "vitest run",
  "test:watch": "vitest"
}
```

Run: `docker exec test-manager-frontend npm install`

Expected: deps installed.

- [ ] **Step 2: Create vitest config**

Create `test-manager-frontend/vitest.config.ts`:

```typescript
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["__tests__/**/*.test.{ts,tsx}"],
  },
});
```

- [ ] **Step 3: Create setup file**

Create `test-manager-frontend/vitest.setup.ts`:

```typescript
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 4: Smoke-test with a trivial test**

Create `test-manager-frontend/__tests__/smoke.test.ts`:

```typescript
import { describe, it, expect } from "vitest";

describe("vitest setup", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

Run: `docker exec test-manager-frontend npm run test`

Expected: 1 passing test.

Remove the smoke test file after the run (we don't need it once the framework is confirmed working):

```bash
rm test-manager-frontend/__tests__/smoke.test.ts
```

---

### Task 6.2: Build `use-analysis-polling` hook (TDD)

**Files:**
- Create: `test-manager-frontend/app/analysis/ai-summary/hooks/use-analysis-polling.ts`
- Create: `test-manager-frontend/__tests__/use-analysis-polling.test.ts`

- [ ] **Step 1: Write failing tests for the polling state machine**

Create `test-manager-frontend/__tests__/use-analysis-polling.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useAnalysisPolling } from "@/app/analysis/ai-summary/hooks/use-analysis-polling";
import type { Analysis } from "@/types/analysis";

function mockAnalysis(overrides: Partial<Analysis> = {}): Analysis {
  return {
    id: "aid",
    schema_version: 1,
    test_id: "t",
    session_id: "s",
    status: "pending",
    created_at: "2026-05-21T14:32:00Z",
    updated_at: "2026-05-21T14:32:00Z",
    kpis: [],
    requirements_check: [],
    logbook_refs: [],
    anomalies: [],
    summary_md: "",
    extra: {},
    ...overrides,
  };
}

describe("useAnalysisPolling", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("does not poll when analysisId is null", () => {
    const fetcher = vi.fn();
    renderHook(() => useAnalysisPolling(null, fetcher));
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("fetches immediately when analysisId is set", async () => {
    const fetcher = vi.fn().mockResolvedValue(mockAnalysis());
    renderHook(() => useAnalysisPolling("aid", fetcher));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
  });

  it("stops polling on terminal status: complete", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(mockAnalysis({ status: "running" }))
      .mockResolvedValueOnce(mockAnalysis({ status: "complete" }))
      .mockResolvedValue(mockAnalysis({ status: "complete" }));

    renderHook(() => useAnalysisPolling("aid", fetcher));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    // Past terminal — no more calls
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("stops polling on terminal status: failed", async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(mockAnalysis({ status: "failed", error: "x" }));
    renderHook(() => useAnalysisPolling("aid", fetcher));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await act(async () => { await vi.advanceTimersByTimeAsync(10000); });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("caps polls at 100", async () => {
    const fetcher = vi.fn().mockResolvedValue(mockAnalysis({ status: "running" }));
    renderHook(() => useAnalysisPolling("aid", fetcher));

    // Advance enough virtual time to trigger 100 poll attempts (3s interval).
    for (let i = 0; i < 102; i++) {
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    }
    expect(fetcher.mock.calls.length).toBeLessThanOrEqual(100);
  });
});
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-frontend npm run test`

Expected: FAIL — `Cannot find module '@/app/analysis/ai-summary/hooks/use-analysis-polling'`.

- [ ] **Step 3: Implement the hook**

Create `test-manager-frontend/app/analysis/ai-summary/hooks/use-analysis-polling.ts`:

```typescript
"use client";

import { useEffect, useRef, useState } from "react";
import type { Analysis } from "@/types/analysis";

const POLL_INTERVAL_MS = 3000;
const BACKOFF_AFTER_MS = 60_000;
const BACKOFF_INTERVAL_MS = 5000;
const MAX_POLLS = 100;

const TERMINAL_STATUSES = new Set(["complete", "failed"]);

export function useAnalysisPolling(
  analysisId: string | null,
  fetcher: (id: string) => Promise<Analysis>,
) {
  const [data, setData] = useState<Analysis | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const pollCount = useRef(0);
  const startedAt = useRef<number | null>(null);

  useEffect(() => {
    if (!analysisId) return;

    let cancelled = false;
    pollCount.current = 0;
    startedAt.current = Date.now();
    setData(null);
    setError(null);

    const tick = async () => {
      if (cancelled) return;
      if (pollCount.current >= MAX_POLLS) return;
      pollCount.current += 1;

      try {
        const result = await fetcher(analysisId);
        if (cancelled) return;
        setData(result);
        if (TERMINAL_STATUSES.has(result.status)) {
          return; // stop scheduling further polls
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e : new Error(String(e)));
        return;
      }

      const elapsed = Date.now() - (startedAt.current ?? Date.now());
      const interval = elapsed > BACKOFF_AFTER_MS ? BACKOFF_INTERVAL_MS : POLL_INTERVAL_MS;
      setTimeout(tick, interval);
    };

    tick();
    return () => { cancelled = true; };
  }, [analysisId, fetcher]);

  return { data, error };
}
```

- [ ] **Step 4: Run tests, confirm green**

Run: `docker exec test-manager-frontend npm run test`

Expected: all 5 polling tests pass.

---

### Task 6.3: Build `TestSessionPicker` component (TDD)

**Files:**
- Create: `test-manager-frontend/app/analysis/ai-summary/components/test-session-picker.tsx`
- Create: `test-manager-frontend/__tests__/test-session-picker.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `test-manager-frontend/__tests__/test-session-picker.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TestSessionPicker } from "@/app/analysis/ai-summary/components/test-session-picker";

const TESTS = [
  { test_id: "TST-1", driver_name: "Daniel" },
  { test_id: "TST-2", driver_name: "Otta" },
];

const SESSIONS_BY_TEST: Record<string, Array<{ session_id: string; track: string; car_model: string }>> = {
  "TST-1": [
    { session_id: "2026-05-21T14:32:00Z", track: "barcelona", car_model: "ferrari" },
    { session_id: "2026-05-21T12:00:00Z", track: "barcelona", car_model: "ferrari" },
  ],
  "TST-2": [],
};

describe("TestSessionPicker", () => {
  it("renders both dropdowns", () => {
    render(<TestSessionPicker
      tests={TESTS}
      sessionsByTest={SESSIONS_BY_TEST}
      selectedTestId={null}
      selectedSessionId={null}
      onChange={vi.fn()}
    />);
    expect(screen.getByLabelText(/test/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/session/i)).toBeInTheDocument();
  });

  it("defaults session to latest session by ISO timestamp desc", () => {
    const onChange = vi.fn();
    render(<TestSessionPicker
      tests={TESTS}
      sessionsByTest={SESSIONS_BY_TEST}
      selectedTestId="TST-1"
      selectedSessionId={null}
      onChange={onChange}
    />);
    // Auto-default fires on mount when sessions exist and selected is null
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      sessionId: "2026-05-21T14:32:00Z",
    }));
  });

  it("shows 'no sessions yet' helper when test has zero sessions", () => {
    render(<TestSessionPicker
      tests={TESTS}
      sessionsByTest={SESSIONS_BY_TEST}
      selectedTestId="TST-2"
      selectedSessionId={null}
      onChange={vi.fn()}
    />);
    expect(screen.getByText(/no sessions/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-frontend npm run test`

Expected: FAIL — component doesn't exist.

- [ ] **Step 3: Implement the component**

Create `test-manager-frontend/app/analysis/ai-summary/components/test-session-picker.tsx`:

```typescript
"use client";

import { useEffect, useMemo } from "react";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface TestSummary {
  test_id: string;
  driver_name?: string | null;
}

export interface SessionSummary {
  session_id: string;
  track: string;
  car_model: string;
}

interface Props {
  tests: TestSummary[];
  sessionsByTest: Record<string, SessionSummary[]>;
  selectedTestId: string | null;
  selectedSessionId: string | null;
  onChange: (sel: { testId: string | null; sessionId: string | null }) => void;
}

export function TestSessionPicker({
  tests,
  sessionsByTest,
  selectedTestId,
  selectedSessionId,
  onChange,
}: Props) {
  const sessions = useMemo(
    () => (selectedTestId ? (sessionsByTest[selectedTestId] ?? []) : []),
    [selectedTestId, sessionsByTest],
  );

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.session_id.localeCompare(a.session_id)),
    [sessions],
  );

  // Auto-pick latest session when test changes and nothing's selected yet
  useEffect(() => {
    if (selectedTestId && !selectedSessionId && sortedSessions.length > 0) {
      onChange({ testId: selectedTestId, sessionId: sortedSessions[0].session_id });
    }
  }, [selectedTestId, selectedSessionId, sortedSessions, onChange]);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      <div>
        <Label htmlFor="picker-test">Test</Label>
        <Select
          value={selectedTestId ?? ""}
          onValueChange={(v) => onChange({ testId: v || null, sessionId: null })}
        >
          <SelectTrigger id="picker-test" className="w-full">
            <SelectValue placeholder="Pick a test..." />
          </SelectTrigger>
          <SelectContent>
            {tests.map((t) => (
              <SelectItem key={t.test_id} value={t.test_id}>
                {t.test_id} {t.driver_name ? `· ${t.driver_name}` : ""}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div>
        <Label htmlFor="picker-session">Session</Label>
        <Select
          value={selectedSessionId ?? ""}
          onValueChange={(v) => onChange({ testId: selectedTestId, sessionId: v || null })}
          disabled={!selectedTestId || sortedSessions.length === 0}
        >
          <SelectTrigger id="picker-session" className="w-full">
            <SelectValue placeholder={
              !selectedTestId ? "Pick a test first" :
              sortedSessions.length === 0 ? "No sessions yet" :
              "Pick a session..."
            } />
          </SelectTrigger>
          <SelectContent>
            {sortedSessions.map((s) => (
              <SelectItem key={s.session_id} value={s.session_id}>
                {s.session_id.slice(0, 16)} · {s.track} / {s.car_model}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selectedTestId && sortedSessions.length === 0 && (
          <p className="text-xs text-muted-foreground mt-1">
            No sessions on this test yet. Start an AC session first.
          </p>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run tests, confirm green**

Run: `docker exec test-manager-frontend npm run test`

Expected: all picker tests pass.

---

### Task 6.4: Build `AnalysisCard` component (TDD)

**Files:**
- Create: `test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx`
- Create: `test-manager-frontend/__tests__/analysis-card.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `test-manager-frontend/__tests__/analysis-card.test.tsx`:

```typescript
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { AnalysisCard } from "@/app/analysis/ai-summary/components/analysis-card";
import type { Analysis } from "@/types/analysis";

function fullAnalysis(): Analysis {
  return {
    id: "aid",
    schema_version: 1,
    test_id: "TST-1",
    session_id: "2026-05-21T14:32:00Z",
    status: "complete",
    created_at: "2026-05-21T15:01:18Z",
    updated_at: "2026-05-21T15:01:51Z",
    model: "claude-opus-4-7",
    tokens_in: 4218,
    tokens_out: 1132,
    duration_ms: 33327,
    quix_session_id: "qsess-abc",
    kpis: [
      { name: "best_lap", value: "1:45.321", unit: "lap" },
      { name: "top_speed", value: 213.4, unit: "km/h" },
    ],
    requirements_check: [
      { requirement: "Lap < 1:46", met: true, evidence: "best 1:45.3" },
      { requirement: "Tyres < 95C", met: false, evidence: "RR=102C lap 8" },
      { requirement: "No off-track", met: null, evidence: "needs feedback" },
    ],
    logbook_refs: ["lb-1"],
    anomalies: [
      { severity: "warn", kind: "brake_spike", lap: 7, description: "FR 612C" },
    ],
    summary_md: "## Pace\n\nGreat session.",
    extra: {},
  };
}

describe("AnalysisCard", () => {
  it("renders KPI tiles", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/best_lap/)).toBeInTheDocument();
    expect(screen.getByText(/1:45.321/)).toBeInTheDocument();
    expect(screen.getByText(/213.4/)).toBeInTheDocument();
  });

  it("renders requirements pills with tri-state styling", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/Lap < 1:46/)).toBeInTheDocument();
    expect(screen.getByText(/Tyres < 95C/)).toBeInTheDocument();
    expect(screen.getByText(/No off-track/)).toBeInTheDocument();
  });

  it("renders anomaly description", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/FR 612C/)).toBeInTheDocument();
  });

  it("renders Markdown narrative", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/Great session/)).toBeInTheDocument();
  });

  it("renders footer with model + tokens + duration", () => {
    render(<AnalysisCard analysis={fullAnalysis()} />);
    expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
    expect(screen.getByText(/4218/)).toBeInTheDocument();
    expect(screen.getByText(/1132/)).toBeInTheDocument();
    expect(screen.getByText(/33s/)).toBeInTheDocument();
  });

  it("handles empty kpis/anomalies gracefully", () => {
    const empty: Analysis = { ...fullAnalysis(), kpis: [], anomalies: [], requirements_check: [] };
    render(<AnalysisCard analysis={empty} />);
    expect(screen.getByText(/Great session/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Confirm failure**

Run: `docker exec test-manager-frontend npm run test`

Expected: FAIL.

- [ ] **Step 3: Install react-markdown if not already**

```bash
docker exec test-manager-frontend npm install react-markdown rehype-sanitize
```

- [ ] **Step 4: Implement AnalysisCard**

Create `test-manager-frontend/app/analysis/ai-summary/components/analysis-card.tsx`:

```typescript
"use client";

import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import { Card } from "@/components/ui/card";
import type { Analysis } from "@/types/analysis";

function formatDuration(ms: number | null | undefined): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${Math.round(ms / 1000)}s`;
}

function MetVerdict({ met }: { met: boolean | null | undefined }) {
  if (met === true)
    return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-green-500/10 text-green-700 text-xs">✓ met</span>;
  if (met === false)
    return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/10 text-red-700 text-xs">✗ unmet</span>;
  return <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-muted text-muted-foreground text-xs">? undetermined</span>;
}

const SEVERITY_STYLES: Record<string, string> = {
  info:  "bg-blue-500/10 text-blue-700",
  warn:  "bg-amber-500/10 text-amber-700",
  error: "bg-red-500/10 text-red-700",
};

export function AnalysisCard({ analysis }: { analysis: Analysis }) {
  return (
    <Card className="p-6 space-y-6">
      <header className="text-sm text-muted-foreground">
        {analysis.id} · {analysis.session_id.slice(0, 16)}
      </header>

      {/* KPI grid */}
      {analysis.kpis.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold mb-2">KPIs</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {analysis.kpis.map((k) => (
              <div key={k.name} className="p-3 rounded-md bg-muted">
                <div className="text-xs text-muted-foreground">{k.name}</div>
                <div className="text-lg font-semibold">{k.value}</div>
                {k.unit && <div className="text-xs text-muted-foreground">{k.unit}</div>}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Requirements pills */}
      {analysis.requirements_check.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold mb-2">Requirements</h3>
          <div className="space-y-1.5">
            {analysis.requirements_check.map((r, i) => (
              <div key={i} className="flex items-center gap-3 text-sm">
                <MetVerdict met={r.met} />
                <span>{r.requirement}</span>
                {r.evidence && <span className="text-xs text-muted-foreground">— {r.evidence}</span>}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Anomalies */}
      {analysis.anomalies.length > 0 && (
        <section>
          <h3 className="text-sm font-semibold mb-2">Anomalies</h3>
          <ul className="space-y-1.5">
            {analysis.anomalies.map((a, i) => (
              <li key={i} className="flex items-start gap-3 text-sm">
                <span className={`inline-flex shrink-0 px-2 py-0.5 rounded-full text-xs ${SEVERITY_STYLES[a.severity] ?? ""}`}>
                  {a.severity}
                </span>
                <span className="font-mono text-xs">{a.kind}</span>
                {a.lap !== null && a.lap !== undefined && (
                  <span className="text-xs text-muted-foreground">L{a.lap}</span>
                )}
                <span>{a.description}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Markdown narrative */}
      {analysis.summary_md && (
        <section className="prose prose-sm max-w-none">
          <ReactMarkdown rehypePlugins={[rehypeSanitize]}>{analysis.summary_md}</ReactMarkdown>
        </section>
      )}

      {/* Footer */}
      <footer className="text-xs text-muted-foreground border-t pt-3 flex flex-wrap gap-3">
        {analysis.model && <span>{analysis.model}</span>}
        {(analysis.tokens_in !== null && analysis.tokens_out !== null) && (
          <span>{analysis.tokens_in}→{analysis.tokens_out} tok</span>
        )}
        <span>{formatDuration(analysis.duration_ms)}</span>
        {analysis.quix_session_id && <span>session {analysis.quix_session_id}</span>}
      </footer>
    </Card>
  );
}
```

- [ ] **Step 5: Run tests, confirm green**

Run: `docker exec test-manager-frontend npm run test`

Expected: all card tests pass.

---

### Task 6.5: Build `AnalyzeButton` component + ai-summary page

**Files:**
- Create: `test-manager-frontend/app/analysis/ai-summary/components/analyze-button.tsx`
- Create: `test-manager-frontend/app/analysis/ai-summary/page.tsx`

- [ ] **Step 1: Create the Analyze button**

Create `test-manager-frontend/app/analysis/ai-summary/components/analyze-button.tsx`:

```typescript
"use client";

import { Button } from "@/components/ui/button";

interface Props {
  disabled: boolean;
  isAnalyzing: boolean;
  hasExistingAnalysis: boolean;
  onClick: () => void;
}

export function AnalyzeButton({ disabled, isAnalyzing, hasExistingAnalysis, onClick }: Props) {
  return (
    <Button onClick={onClick} disabled={disabled || isAnalyzing}>
      {isAnalyzing ? "Analyzing..." : hasExistingAnalysis ? "Re-analyze" : "Analyze"}
    </Button>
  );
}
```

- [ ] **Step 2: Create the host page that ties everything together**

Create `test-manager-frontend/app/analysis/ai-summary/page.tsx`:

```typescript
"use client";

import { useEffect, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useTestsApi } from "@/lib/hooks/use-api";
import { useAnalysesApi } from "@/lib/api/analyses";
import { useToast } from "@/lib/hooks/use-toast";
import { TestSessionPicker } from "./components/test-session-picker";
import { AnalysisCard } from "./components/analysis-card";
import { AnalyzeButton } from "./components/analyze-button";
import { useAnalysisPolling } from "./hooks/use-analysis-polling";
import type { Analysis } from "@/types/analysis";

export default function AiSummaryPage() {
  const params = useSearchParams();
  const router = useRouter();
  const { toast } = useToast();
  const testsApi = useTestsApi();
  const analysesApi = useAnalysesApi();

  const [tests, setTests] = useState<Array<{ test_id: string; driver_name?: string | null }>>([]);
  const [sessionsByTest, setSessionsByTest] = useState<Record<string, Array<{ session_id: string; track: string; car_model: string }>>>({});
  const [history, setHistory] = useState<Analysis[]>([]);
  const [activeAnalysisId, setActiveAnalysisId] = useState<string | null>(null);

  const selectedTestId = params.get("test_id");
  const selectedSessionId = params.get("session_id");

  const handlePickerChange = useCallback((sel: { testId: string | null; sessionId: string | null }) => {
    const next = new URLSearchParams(params);
    next.set("tab", "ai-summary");
    if (sel.testId) next.set("test_id", sel.testId); else next.delete("test_id");
    if (sel.sessionId) next.set("session_id", sel.sessionId); else next.delete("session_id");
    router.push(`/analysis?${next.toString()}`);
  }, [params, router]);

  // Load tests on mount
  useEffect(() => {
    testsApi.list({ page: 1, page_size: 200 })
      .then((res) => setTests(res.items.map((t: any) => ({ test_id: t.test_id, driver_name: t.driver_name }))))
      .catch((e) => toast({ title: "Failed to load tests", description: String(e), variant: "destructive" }));
  }, [testsApi, toast]);

  // Load sessions for the selected test
  useEffect(() => {
    if (!selectedTestId) return;
    if (sessionsByTest[selectedTestId]) return;
    testsApi.get(selectedTestId)
      .then((t: any) => setSessionsByTest((cur) => ({ ...cur, [selectedTestId]: t.sessions })))
      .catch((e) => toast({ title: "Failed to load sessions", description: String(e), variant: "destructive" }));
  }, [selectedTestId, sessionsByTest, testsApi, toast]);

  // Load history of analyses for the selected (test, session)
  useEffect(() => {
    if (!selectedTestId || !selectedSessionId) {
      setHistory([]);
      return;
    }
    analysesApi.list({ testId: selectedTestId, sessionId: selectedSessionId })
      .then((res) => setHistory(res.items))
      .catch((e) => toast({ title: "Failed to load history", description: String(e), variant: "destructive" }));
  }, [selectedTestId, selectedSessionId, analysesApi, activeAnalysisId, toast]);

  // Polling for the currently-running analysis
  const fetcher = useCallback(
    (id: string) => analysesApi.get(id),
    [analysesApi],
  );
  const { data: polled, error: polledError } = useAnalysisPolling(activeAnalysisId, fetcher);

  // Display: latest from history OR the actively-polling one if newer
  const displayed: Analysis | null = polled ?? (history.length > 0 ? history[0] : null);
  const isAnalyzing = polled?.status !== undefined && !["complete", "failed"].includes(polled.status);

  const onAnalyze = useCallback(async () => {
    if (!selectedTestId || !selectedSessionId) return;
    try {
      const { analysis_id } = await analysesApi.create({
        test_id: selectedTestId,
        session_id: selectedSessionId,
      });
      setActiveAnalysisId(analysis_id);
    } catch (e) {
      toast({ title: "Failed to start analysis", description: String(e), variant: "destructive" });
    }
  }, [selectedTestId, selectedSessionId, analysesApi, toast]);

  return (
    <div className="space-y-6 p-6">
      <TestSessionPicker
        tests={tests}
        sessionsByTest={sessionsByTest}
        selectedTestId={selectedTestId}
        selectedSessionId={selectedSessionId}
        onChange={handlePickerChange}
      />

      <div className="flex justify-end">
        <AnalyzeButton
          disabled={!selectedTestId || !selectedSessionId}
          isAnalyzing={isAnalyzing}
          hasExistingAnalysis={history.length > 0}
          onClick={onAnalyze}
        />
      </div>

      {displayed ? (
        <AnalysisCard analysis={displayed} />
      ) : selectedSessionId ? (
        <p className="text-sm text-muted-foreground">No analyses yet for this session. Click Analyze to start one.</p>
      ) : (
        <p className="text-sm text-muted-foreground">Pick a test and a session, then click Analyze.</p>
      )}

      {polledError && (
        <p className="text-sm text-destructive">Polling failed: {polledError.message}</p>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `docker exec test-manager-frontend npm run type-check`

Expected: 0 errors. (If the existing `useTestsApi` hook doesn't match the shape used here, adapt the method names to match what's in `lib/hooks/use-api.ts`.)

---

### Task 6.6: Drop stub sub-tabs and register AI Summary sub-tab in analysis page

**Files:**
- Modify: `test-manager-frontend/app/analysis/page.tsx`

- [ ] **Step 1: Find the sub-tab definition**

Open `test-manager-frontend/app/analysis/page.tsx`. Find the array or list defining the sub-tabs (Compare, Per-Corner, Live, Single Run, Leaderboard, Notebook).

- [ ] **Step 2: Remove the four unused stubs and add AI Summary**

Replace the sub-tab definitions to keep only:
- Compare (existing)
- Leaderboard (existing)
- AI Summary (new)

Add the new tab pointing to the new page component. The exact structure depends on the existing implementation (Next.js App Router segments or Tabs component); follow whichever pattern is there. If `app/analysis/[tab]/page.tsx` is dynamic, add a case for `ai-summary` rendering the new `app/analysis/ai-summary/page.tsx`. If the existing analysis page uses a `<Tabs>` component with hardcoded children, add an `<TabsContent value="ai-summary">` block importing the new page.

- [ ] **Step 3: Type-check + visual check**

Run: `docker exec test-manager-frontend npm run type-check`

Boot the dev stack, navigate to `/analysis?tab=ai-summary`, verify the page loads. Verify Per-Corner / Live / Single Run / Notebook tabs are gone.

---

### Task 6.7: Add AI Summary deep-link buttons in test-detail-card

**Files:**
- Modify: `test-manager-frontend/components/tests/test-detail-card.tsx`

- [ ] **Step 1: Add a sibling AI Summary button**

Find the existing `handleAnalyze` function (around line 45-64) that pushes to `/analysis?tab=compare&test_id=...`. Add a second handler + button next to it:

```typescript
const handleAiSummary = (sessionId?: string) => {
  const params = new URLSearchParams();
  params.set("tab", "ai-summary");
  params.set("test_id", test.test_id);
  if (sessionId) params.set("session_id", sessionId);
  router.push(`/analysis?${params.toString()}`);
};

// In the JSX, next to the existing Analyze Telemetry button:
<Button onClick={() => handleAiSummary()} variant="outline">
  AI Summary
</Button>
```

If a Sessions list is rendered on this card, add a per-row "AI Summary" link that calls `handleAiSummary(session.session_id)`.

- [ ] **Step 2: Type-check**

Run: `docker exec test-manager-frontend npm run type-check`

Expected: 0 errors.

---

### Task 6.8: Playwright E2E for AI Summary + logbook session

**Files:**
- Create: `test-manager-frontend/e2e/ai-summary.spec.ts`
- Create: `test-manager-frontend/e2e/logbook-session.spec.ts`

- [ ] **Step 1: Write E2E for AI Summary basic flow (with mocked backend)**

Create `test-manager-frontend/e2e/ai-summary.spec.ts`. Mirror the existing pattern in `e2e/tests.spec.ts` for backend mocking via `page.route(...)`:

```typescript
import { test, expect } from "@playwright/test";

test.describe("AI Summary sub-tab", () => {
  test("picker → analyze → polling → render", async ({ page }) => {
    // Mock backend
    await page.route("**/api/v1/tests*", (route) => {
      route.fulfill({
        json: { items: [{ test_id: "TST-1", driver_name: "Daniel" }], total: 1, page: 1, page_size: 20 },
      });
    });
    await page.route("**/api/v1/tests/TST-1", (route) => {
      route.fulfill({
        json: {
          test_id: "TST-1",
          driver_name: "Daniel",
          sessions: [{ session_id: "2026-05-21T14:32:00Z", track: "barcelona", car_model: "ferrari" }],
        },
      });
    });
    await page.route("**/api/v1/analyses?test_id=*", (route) => {
      route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 20 } });
    });
    let pollCount = 0;
    await page.route("**/api/v1/analyses/aid-1", (route) => {
      pollCount++;
      const status = pollCount >= 2 ? "complete" : "running";
      route.fulfill({
        json: {
          id: "aid-1", schema_version: 1, test_id: "TST-1", session_id: "2026-05-21T14:32:00Z",
          status, created_at: "...", updated_at: "...",
          kpis: status === "complete" ? [{ name: "best_lap", value: "1:45.321" }] : [],
          requirements_check: [], logbook_refs: [], anomalies: [],
          summary_md: status === "complete" ? "Done." : "", extra: {},
          model: "claude-opus-4-7", tokens_in: 100, tokens_out: 50, duration_ms: 30000,
        },
      });
    });
    await page.route("**/api/v1/analyses", (route) => {
      if (route.request().method() === "POST") {
        route.fulfill({ status: 202, json: { analysis_id: "aid-1" } });
      }
    });

    await page.goto("/analysis?tab=ai-summary");
    await page.getByLabel(/test/i).click();
    await page.getByRole("option", { name: /TST-1/ }).click();
    // Session auto-selected
    await page.getByRole("button", { name: /^Analyze$/ }).click();
    await expect(page.getByText(/best_lap/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/1:45.321/)).toBeVisible();
  });
});
```

- [ ] **Step 2: Write E2E for logbook session badge**

Create `test-manager-frontend/e2e/logbook-session.spec.ts`. Mock the tests endpoint to return a test with two sessions, mock `/api/v1/tests/{id}/logbook` to return entries with various `session_id` values, navigate to the test detail page's Logbook tab, verify badges render:

```typescript
import { test, expect } from "@playwright/test";

test("logbook list shows session badge per entry", async ({ page }) => {
  const testId = "TST-1";
  const sessionId = "2026-05-21T14:32:00.000Z";
  await page.route(`**/api/v1/tests/${testId}/full`, (route) => {
    route.fulfill({
      json: {
        test: {
          test_id: testId, driver_name: "Daniel",
          sessions: [{ session_id: sessionId, track: "barcelona", car_model: "ferrari" }],
        },
        logbook: [
          { id: "lb-1", test_id: testId, session_id: sessionId, content: "tied", created_at: "..." },
          { id: "lb-2", test_id: testId, session_id: null, content: "wide", created_at: "..." },
        ],
      },
    });
  });
  await page.goto(`/tests/${testId}`);
  // Click logbook tab if needed; depending on UI, badges should be visible:
  await expect(page.getByText(/2026-05-21T14:32/)).toBeVisible();
  await expect(page.getByText(/Test-wide/)).toBeVisible();
});
```

- [ ] **Step 3: Run Playwright tests**

Run: `docker exec test-manager-frontend npm run test:e2e`

Expected: both new specs pass.

---

### Task 6.9: Run Phase 6 gates + commit

- [ ] **Step 1: Frontend full gates**

Run: `docker exec test-manager-frontend npm run lint && docker exec test-manager-frontend npm run type-check && docker exec test-manager-frontend npm run test && docker exec test-manager-frontend npm run build`

Expected: all green; `next build` succeeds.

- [ ] **Step 2: Commit Phase 6**

```bash
git add test-manager-frontend/

git commit -m "Add AI Summary sub-tab and analyses frontend"
```

Expected: clean.

---

**Phase 6 complete.** Frontend feature complete. Continue to Phase 7 (quix-ai-config scripts).


---

# Phase 7 — quix-ai-config folder, scripts, system prompt, KBs

**Goal:** Land the canonical `quix-ai-config/` folder with shared scripts (update_agent.py, update_kb_resource.py, etc.), the Post-Race Analyzer agent config + system prompt + KB markdowns, and a setup runbook in README.

**Commit at end:** `Add quix-ai-config scripts and post-race agent assets`

---

### Task 7.1: Create folder skeleton + README

**Files:**
- Create: `quix-ai-config/README.md`
- Create: `quix-ai-config/scripts/` (dir)
- Create: `quix-ai-config/post-race/` (dir)
- Create: `quix-ai-config/post-race/kb/` (dir)

- [ ] **Step 1: Create the directory structure + README**

Run:

```bash
mkdir -p /Users/daniel/repos/ac-quix-bridge/quix-ai-config/scripts
mkdir -p /Users/daniel/repos/ac-quix-bridge/quix-ai-config/post-race/kb
```

Create `quix-ai-config/README.md`:

```markdown
# quix-ai-config

Source of truth for Quix.AI agent configurations, knowledge bases, and MCP server registrations used by the AC telemetry pipeline.

This folder is **NOT a Quix Cloud deployment.** Quix Portal scans only deployments listed in top-level `quix.yaml`. The scripts here are hand-run from a developer machine to push config to Quix.AI's REST API.

## Folder map

```
quix-ai-config/
├── README.md
├── scripts/                              # shared across all agents
│   ├── update_agent.py                   # push agent config (system prompt + tool filter + KB refs)
│   ├── update_kb_resource.py             # push a single KB markdown file
│   ├── bind_kb_to_agent.py               # bind one or more KBs to one agent
│   ├── register_mcp.py                   # register an MCP server in the org config
│   ├── list_agents.py                    # debug: list all org agents
│   └── list_kbs.py                       # debug: list all org KBs
└── post-race/                            # per-agent assets for "Post-Race Analyzer"
    ├── system_prompt.md                  # canonical narrative prompt
    └── kb/
        ├── analysis_contract.md          # SaveAnalysisPayload field semantics
        └── tm_schema.md                  # Test/SessionInfo/LogbookEntry shapes
```

## One-time setup runbook

Set env vars first (read by all scripts):

```bash
export QUIX_PORTAL_API=https://portal-api.platform.quix.io
export QUIX_TOKEN=<personal access token>
export QUIX_WORKSPACE_ID=<workspace-id>
```

Then in order:

```bash
# 1. Register the test-manager MCP server in Quix.AI org config
python scripts/register_mcp.py \
    --name test-manager \
    --display-name "Test Manager" \
    --url "https://test-manager-backend-<project>.<env>.quix.io/mcp" \
    --api-key "$(openssl rand -hex 32)"
# Writes server_id to .env and prints the API key — copy the key into
# the test-manager-backend deployment env as TESTMANAGER_MCP_API_KEY.

# 2. Push the two new KBs
python scripts/update_kb_resource.py post-race/kb/analysis_contract.md
python scripts/update_kb_resource.py post-race/kb/tm_schema.md
# Each writes the KB ID to .env (ANALYSIS_CONTRACT_KB_ID, TM_SCHEMA_KB_ID).

# 3. Push the agent config (idempotent — creates if not exists, updates if exists)
python scripts/update_agent.py
# Writes QUIX_AI_POST_RACE_AGENT_ID to .env.

# 4. Set the two new env vars in test-manager-backend deployment via Quix Portal UI:
#    TESTMANAGER_MCP_API_KEY      (from step 1)
#    QUIX_AI_POST_RACE_AGENT_ID   (from step 3)
# Then redeploy the backend.
```

Any subsequent change to system prompt or KBs:

```bash
python scripts/update_kb_resource.py post-race/kb/<changed-file>.md
python scripts/update_agent.py
```

Both are idempotent — re-running with no changes is a no-op.

## Probes

Debug probes (originally in `quix-ai-exploration/probes/`) can be moved here later as a separate cleanup PR. Out of scope for this initial commit.
```

- [ ] **Step 2: Verify dir + file exist**

Run: `ls quix-ai-config/ && ls quix-ai-config/scripts/ && ls quix-ai-config/post-race/`

Expected: README.md visible, scripts/ + post-race/kb/ directories exist (empty for now).

---

### Task 7.2: Write `system_prompt.md`

**Files:**
- Create: `quix-ai-config/post-race/system_prompt.md`

- [ ] **Step 1: Write the canonical system prompt**

Create `quix-ai-config/post-race/system_prompt.md`:

```markdown
# Post-Race Analyzer

You analyze a single completed racing session in the AC telemetry pipeline. You produce a structured + narrative report and persist it via the `save_analysis` MCP tool.

## Hard rules

1. You MUST call `mcp__test-manager__save_analysis` exactly once before ending your reply.
2. You MUST pass the `analysis_id` you receive in the user message to `save_analysis`.
3. Always call `mcp__test-manager__list_logbook` with `include_test_wide=true` on the first turn — pre-session prep notes are relevant context.
4. Always partition-filter SQL queries on `session_id`, and where applicable `experiment`, `test_rig`, `environment`, `driver`. Unfiltered SELECTs scan the whole lake.
5. `iCurrentTime` carries across driver switches within a session_id — use `MAX(ts_ms) - MIN(ts_ms)` for wall-clock duration, never a sum of lap times.
6. Lap 1 is the out-lap. Exclude from best-lap / avg-lap calculations unless explicitly relevant.
7. Prefer SQL via `mcp__quixlake__*` for KPIs and anomaly detection. Use `delegate_task` ONLY when SQL cannot express the analysis (derivatives, FFT, cross-session correlation in Python).
8. Never invent values. If a KPI cannot be measured, omit it. If a requirement is subjective, set `met: null` with an explanation in `evidence`.

## Workflow

1. Read `analysis_id`, `test_id`, `session_id` from the user message.
2. Fetch test context with `mcp__test-manager__get_test(test_id)` — read `requirements` text, resolved driver/device/env names.
3. Fetch logbook with `mcp__test-manager__list_logbook(test_id, session_id, include_test_wide=true)`.
4. Query the lake for KPIs scoped to this `session_id`. Useful queries:
   - Best lap: `MIN(iLastTimeMs) WHERE iCurrentLap > 1 AND iLastTimeMs > 0`
   - Top speed: `MAX(speedKmh)`
   - Per-wheel tyre/brake peaks: `MAX(tyreTempFL)`, etc.
5. Parse the free-text `requirements` field into discrete checks. Verify each against the KPIs. Be honest — failed requirements stay failed.
6. Scan for anomalies: brake spikes (>600°C), tyre overheats (>100°C), telemetry gaps (gaps between consecutive `ts_ms` rows > 1000ms), off-track flags if present in schema.
7. For derivative or cross-session anomaly checks SQL can't express, call `delegate_task` with `workspace_id` set from the session context. Use sparingly.
8. Compose `summary_md` narrative. Suggested sections (`## Pace`, `## Requirements`, `## Anomalies`, `## Driver feedback`, `## Recommendations`). Reference logbook entries by ID in `logbook_refs`.
9. Call `mcp__test-manager__save_analysis(analysis_id, payload)` with all populated fields. Return briefly.

## Output contract

See the "Analysis Contract" knowledge base for the full `SaveAnalysisPayload` schema. Key reminders:

- `kpis`: list of `{name, value, unit?, notes?}`. KPI names are loose strings — use domain-natural names like `best_lap`, `top_speed_kmh`, `avg_brake_temp_FR_c`.
- `requirements_check`: list of `{requirement, met, evidence?}`. `met` is `true` / `false` / `null` (undetermined).
- `anomalies`: list of `{severity, kind, lap?, time_ms?, description, evidence?}`. Severity = `info` / `warn` / `error`.
- `logbook_refs`: list of LogbookEntry IDs (the `id` field from `list_logbook` results) you cited.
- `summary_md`: required Markdown narrative.
- `extra`: free-form dict for anything that doesn't fit (weather, setup deltas, etc.).
```

---

### Task 7.3: Write the two KB markdown files

**Files:**
- Create: `quix-ai-config/post-race/kb/analysis_contract.md`
- Create: `quix-ai-config/post-race/kb/tm_schema.md`

- [ ] **Step 1: Write `analysis_contract.md`**

Create `quix-ai-config/post-race/kb/analysis_contract.md`:

```markdown
# Analysis Contract

The `save_analysis` MCP tool accepts a `SaveAnalysisPayload` shape. Each field semantics:

## analysis_id (required, string)

The opaque UUID passed to you in the user message. You must pass it back unchanged.

## summary_md (required, Markdown string, min 1 character)

The narrative spine of your analysis. The only required content field. If everything else is uncertain, write `summary_md` and leave other lists empty.

Suggested section headers: `## Pace`, `## Requirements`, `## Anomalies`, `## Driver feedback`, `## Recommendations`.

## kpis (optional, list of KpiValue)

One entry per measurable. Names are opaque strings; the UI displays whatever you emit.

### KpiValue shape

- `name` (string, required): e.g. `best_lap`, `top_speed_kmh`, `avg_brake_temp_FR_c`
- `value` (number or string, required): e.g. `1.45321` or `"1:45.321"`. Prefer numbers when meaningful.
- `unit` (optional string): e.g. `"s"`, `"km/h"`, `"°C"`, `"lap"`
- `notes` (optional string): caveats, e.g. `"laps 2-12 only — lap 1 excluded as out-lap"`

### Worked KpiValue example

```json
{"name": "best_lap", "value": "1:45.321", "unit": "lap", "notes": "lap 6"}
{"name": "max_brake_temp_FR", "value": 612.0, "unit": "°C", "notes": "spike lap 7 entry T1"}
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
- `kind` (string, required): opaque tag, e.g. `brake_spike`, `tyre_overheat`, `telemetry_gap`, `off_track`
- `lap` (optional int): lap number
- `time_ms` (optional int): ms from session start
- `description` (string, required): human-readable
- `evidence` (optional string): SQL row or computed value

## logbook_refs (optional, list of strings)

LogbookEntry `id` values you cited in your narrative. Refer to entries by their UUID.

## extra (optional, dict)

Free-form bag for observations that don't fit a defined field. E.g. weather, setup deltas, mechanical notes. Keys are descriptive strings.

## Worked complete example

```json
{
  "analysis_id": "f47ac10b-58cc-...",
  "summary_md": "## Pace\nGood session...\n\n## Requirements\n...",
  "kpis": [
    {"name": "best_lap", "value": "1:45.321", "unit": "lap"},
    {"name": "top_speed_kmh", "value": 213.4, "unit": "km/h"}
  ],
  "requirements_check": [
    {"requirement": "Complete 10 laps", "met": true, "evidence": "12 racing laps recorded"},
    {"requirement": "Tyres < 95°C", "met": false, "evidence": "RR peaked 102°C laps 8-9"}
  ],
  "anomalies": [
    {"severity": "warn", "kind": "brake_spike", "lap": 7, "time_ms": 723000,
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
```

- [ ] **Step 2: Write `tm_schema.md`**

Create `quix-ai-config/post-race/kb/tm_schema.md`:

```markdown
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
```

---

### Task 7.4: Write the setup scripts

**Files:**
- Create: `quix-ai-config/scripts/update_agent.py`
- Create: `quix-ai-config/scripts/update_kb_resource.py`
- Create: `quix-ai-config/scripts/bind_kb_to_agent.py`
- Create: `quix-ai-config/scripts/register_mcp.py`
- Create: `quix-ai-config/scripts/list_agents.py`
- Create: `quix-ai-config/scripts/list_kbs.py`
- Create: `quix-ai-config/scripts/_common.py`

These scripts are hand-run from a developer machine; they need to be self-contained and only depend on stdlib + `httpx`.

- [ ] **Step 1: Create shared helper module**

Create `quix-ai-config/scripts/_common.py`:

```python
"""Shared helpers for Quix.AI setup scripts.

Reads env vars:
  QUIX_PORTAL_API   - e.g. https://portal-api.platform.quix.io
  QUIX_TOKEN        - personal access token
  QUIX_WORKSPACE_ID - workspace id (optional for some endpoints)

Persists IDs to a local `.env` file in the script's working directory so
subsequent scripts can chain.
"""

from __future__ import annotations

import os
import pathlib
import re

import httpx

ENV_FILE = pathlib.Path(__file__).resolve().parent.parent / ".env"


def portal() -> str:
    url = os.environ.get("QUIX_PORTAL_API", "").rstrip("/")
    if not url:
        raise SystemExit("QUIX_PORTAL_API not set")
    return url


def token() -> str:
    t = os.environ.get("QUIX_TOKEN", "")
    if not t:
        raise SystemExit("QUIX_TOKEN not set")
    return t


def headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token()}",
        "Content-Type": "application/json",
    }


def http_client() -> httpx.Client:
    return httpx.Client(base_url=portal(), headers=headers(), timeout=60.0)


def write_env(key: str, value: str) -> None:
    """Append-or-update `KEY=VALUE` in the local .env file."""
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text().splitlines()

    pattern = re.compile(rf"^{re.escape(key)}=")
    new_lines = [line for line in lines if not pattern.match(line)]
    new_lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(new_lines) + "\n")
    print(f"  wrote {key}={value} → {ENV_FILE}")


def read_env_value(key: str) -> str | None:
    """Read a value previously stashed by write_env."""
    if not ENV_FILE.exists():
        return None
    pattern = re.compile(rf"^{re.escape(key)}=(.*)$")
    for line in ENV_FILE.read_text().splitlines():
        m = pattern.match(line)
        if m:
            return m.group(1)
    return None
```

- [ ] **Step 2: Create `register_mcp.py`**

Create `quix-ai-config/scripts/register_mcp.py`:

```python
"""Register an MCP server in the Quix.AI org config.

Usage:
    python register_mcp.py \
        --name test-manager \
        --display-name "Test Manager" \
        --url https://test-manager-backend-...quix.io/mcp \
        --api-key <generated>
"""

from __future__ import annotations

import argparse
import sys

from _common import http_client, write_env


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="slug — tools become mcp__<name>__<tool>")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--url", required=True, help="public URL of MCP endpoint")
    parser.add_argument("--api-key", required=True, help="shared X-API-Key secret")
    args = parser.parse_args(argv)

    with http_client() as client:
        # First, see if a server with this name already exists.
        existing = client.get("/api/user/mcp-servers").json()
        match = next((s for s in existing if s.get("name") == args.name), None)

        body = {
            "name": args.name,
            "displayName": args.display_name,
            "url": args.url,
            "auth": {
                "type": "api_key",
                "headerName": "X-API-Key",
                "credential": args.api_key,
            },
        }

        if match:
            server_id = match["id"]
            print(f"Updating existing MCP server {server_id} (name={args.name})")
            resp = client.put(f"/api/user/mcp-servers/{server_id}", json=body)
        else:
            print(f"Creating new MCP server (name={args.name})")
            resp = client.post("/api/user/mcp-servers", json=body)

        resp.raise_for_status()
        server_id = resp.json()["id"]

    write_env("TESTMANAGER_MCP_SERVER_ID", server_id)
    print(f"\nDone. API key (set in test-manager-backend env as TESTMANAGER_MCP_API_KEY):")
    print(f"  {args.api_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Create `update_kb_resource.py`**

Create `quix-ai-config/scripts/update_kb_resource.py`:

```python
"""Push a single Knowledge Base markdown file to Quix.AI.

Usage:
    python update_kb_resource.py path/to/file.md
"""

from __future__ import annotations

import pathlib
import re
import sys

from _common import http_client, write_env


def _slug(path: pathlib.Path) -> str:
    """KB display name derived from filename: 'analysis_contract.md' → 'Analysis Contract'."""
    stem = path.stem
    return stem.replace("_", " ").replace("-", " ").title()


def _env_key(path: pathlib.Path) -> str:
    """Env var key: 'analysis_contract.md' → 'ANALYSIS_CONTRACT_KB_ID'."""
    return re.sub(r"[^A-Z0-9]", "_", path.stem.upper()) + "_KB_ID"


def main(argv: list[str] | None = None) -> int:
    if not argv:
        argv = sys.argv[1:]
    if not argv:
        print("Usage: update_kb_resource.py <path-to-md>")
        return 2

    md_path = pathlib.Path(argv[0]).resolve()
    if not md_path.is_file():
        print(f"File not found: {md_path}")
        return 1

    name = _slug(md_path)
    content = md_path.read_text()

    with http_client() as client:
        existing = client.get("/ai/api/knowledge-bases").json()
        match = next((kb for kb in existing if kb.get("name") == name), None)
        body = {"name": name, "description": f"Source: {md_path.name}"}

        if match:
            kb_id = match["id"]
            print(f"Updating existing KB {kb_id} (name={name!r})")
            client.put(f"/ai/api/knowledge-bases/{kb_id}", json=body).raise_for_status()
        else:
            print(f"Creating new KB (name={name!r})")
            kb_id = client.post("/ai/api/knowledge-bases", json=body).json()["id"]

        # Upload content as a single resource
        # The exact endpoint shape depends on the deployed Quix.AI version — verify against
        # the openapi at quix-ai-exploration/probes/openapi/openapi.json. Common shape:
        client.put(
            f"/ai/api/knowledge-bases/{kb_id}/resources/main",
            json={"name": "main", "content": content},
        ).raise_for_status()

    write_env(_env_key(md_path), kb_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note: the KB resource-upload endpoint may need adjustment per actual API. Verify by inspecting `/Users/daniel/repos/ac-quix-bridge/quix-ai-exploration/probes/openapi/openapi.json` for the exact route shape before running. The existing `quix-ai-exploration/probes/update_kb_resource.py` may have a working version to copy.

- [ ] **Step 4: Create `update_agent.py`**

Create `quix-ai-config/scripts/update_agent.py`:

```python
"""Create or update the Post-Race Analyzer agent in Quix.AI.

Reads .env for:
  ANALYSIS_CONTRACT_KB_ID
  TM_SCHEMA_KB_ID
  TESTMANAGER_MCP_SERVER_ID (informational — used to allowlist on the MCP server side)

Writes:
  QUIX_AI_POST_RACE_AGENT_ID
"""

from __future__ import annotations

import os
import pathlib
import sys

from _common import http_client, read_env_value, write_env


DISPLAY_NAME = "Post-Race Analyzer"

# Tool filter: explicit allowlist. Confirm exact `mcp__quixlake__*` tool names
# during impl by running `python list_agents.py` to inspect a working agent.
TOOL_FILTER_TOOL_NAMES = [
    "delegate_task",
    # quixlake-mcp — confirm exact server slug + tool names at install time
    "mcp__quixlake__sql",
    "mcp__quixlake__describe_table",
    # our test-manager-mcp tools (slug = "test-manager")
    "mcp__test-manager__get_test",
    "mcp__test-manager__get_session",
    "mcp__test-manager__list_logbook",
    "mcp__test-manager__get_driver",
    "mcp__test-manager__get_device",
    "mcp__test-manager__get_environment",
    "mcp__test-manager__list_sessions_for_test",
    "mcp__test-manager__list_recent_sessions_for_driver",
    "mcp__test-manager__save_analysis",
]


def main(argv: list[str] | None = None) -> int:
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    system_prompt_path = repo_root / "post-race" / "system_prompt.md"
    system_prompt = system_prompt_path.read_text()

    analysis_kb = read_env_value("ANALYSIS_CONTRACT_KB_ID") or os.environ.get("ANALYSIS_CONTRACT_KB_ID")
    tm_schema_kb = read_env_value("TM_SCHEMA_KB_ID") or os.environ.get("TM_SCHEMA_KB_ID")
    ac_kb = os.environ.get("AC_TELEMETRY_KB_ID")  # existing KB, set in shell env

    if not analysis_kb or not tm_schema_kb:
        print("Missing KB IDs in .env. Run update_kb_resource.py first.")
        return 1

    kb_rules = [
        {"knowledgeBaseId": analysis_kb, "accessLevel": "read"},
        {"knowledgeBaseId": tm_schema_kb, "accessLevel": "read"},
    ]
    if ac_kb:
        kb_rules.append({"knowledgeBaseId": ac_kb, "accessLevel": "read"})

    body = {
        "displayName": DISPLAY_NAME,
        "systemPrompt": system_prompt,
        "kbAccessRules": kb_rules,
        "toolFilter": {
            "mode": "whitelist",
            "toolNames": TOOL_FILTER_TOOL_NAMES,
        },
    }

    with http_client() as client:
        existing = client.get("/api/org/agents").json()
        match = next((a for a in existing if a.get("displayName") == DISPLAY_NAME), None)
        if match:
            agent_id = match["id"]
            print(f"Updating existing agent {agent_id} ({DISPLAY_NAME})")
            client.put(f"/api/org/agents/{agent_id}", json=body).raise_for_status()
        else:
            print(f"Creating new agent ({DISPLAY_NAME})")
            agent_id = client.post("/api/org/agents", json=body).json()["id"]

    write_env("QUIX_AI_POST_RACE_AGENT_ID", agent_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Create `bind_kb_to_agent.py`, `list_agents.py`, `list_kbs.py`**

These are thin helpers — mirror existing patterns from `quix-ai-exploration/probes/`. Create simplified versions:

Create `quix-ai-config/scripts/bind_kb_to_agent.py`:

```python
"""Idempotently bind a KB to an agent.

Usage:
    python bind_kb_to_agent.py <agent_id> <kb_id>
"""

import sys
from _common import http_client


def main(argv: list[str] | None = None) -> int:
    if not argv:
        argv = sys.argv[1:]
    if len(argv) != 2:
        print("Usage: bind_kb_to_agent.py <agent_id> <kb_id>")
        return 2

    agent_id, kb_id = argv

    with http_client() as client:
        agent = client.get(f"/api/org/agents/{agent_id}").json()
        rules = agent.get("kbAccessRules", [])
        if any(r["knowledgeBaseId"] == kb_id for r in rules):
            print(f"Agent {agent_id} already has KB {kb_id} bound.")
            return 0
        rules.append({"knowledgeBaseId": kb_id, "accessLevel": "read"})
        client.put(f"/api/org/agents/{agent_id}",
                   json={"kbAccessRules": rules}).raise_for_status()
        print(f"Bound KB {kb_id} to agent {agent_id}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Create `quix-ai-config/scripts/list_agents.py`:

```python
"""List all org agents."""

import json
from _common import http_client


def main() -> int:
    with http_client() as client:
        agents = client.get("/api/org/agents").json()
    print(json.dumps(agents, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `quix-ai-config/scripts/list_kbs.py`:

```python
"""List all org KBs."""

import json
from _common import http_client


def main() -> int:
    with http_client() as client:
        kbs = client.get("/ai/api/knowledge-bases").json()
    print(json.dumps(kbs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Smoke-test script imports work (don't execute against real API)**

Run from your local dev machine (not docker, since these are operator scripts):

```bash
cd quix-ai-config/scripts
python -c "import update_agent, update_kb_resource, bind_kb_to_agent, register_mcp, list_agents, list_kbs"
```

Expected: no errors (just import smoke test — no API call made).

If `httpx` is missing locally: `pip install httpx`.

---

### Task 7.5: Add `.gitignore` entry to ignore `quix-ai-config/.env`

**Files:**
- Modify: `.gitignore`

- [ ] **Step 1: Append rule**

Add to the repo-root `.gitignore`:

```
# quix-ai-config writes script-state to its own .env
quix-ai-config/.env
```

---

### Task 7.6: Run Phase 7 gates + commit

- [ ] **Step 1: No automated gates for this phase**

Scripts run against a remote API; no unit-test target. The smoke-import test in 7.4-step-6 is sufficient.

- [ ] **Step 2: Commit Phase 7**

```bash
git add quix-ai-config/ .gitignore

git commit -m "Add quix-ai-config scripts and post-race agent assets"
```

Expected: clean.

---

**Phase 7 complete.** All 7 phases done. Run the pre-push gate suite next:

```bash
# Backend
docker exec test-manager-backend uv run ruff check .
docker exec test-manager-backend uv run ruff format --check .
docker exec test-manager-backend uv run ty check
docker exec test-manager-backend uv run pytest

# Frontend
docker exec test-manager-frontend npm run lint
docker exec test-manager-frontend npm run type-check
docker exec test-manager-frontend npm run test
docker exec test-manager-frontend npm run build
docker exec test-manager-frontend npm run test:e2e
```

Then the manual cloud round per spec §9 before merging to `feature/test-manager`.

---

## Self-Review (post-write)

**Spec coverage:**
- §4.1 Logbook session_id rework: Phase 1 ✓
- §4.2 Analyses model: Phase 2 ✓
- §4.3 Analyses routes: Phase 3 ✓
- §4.4 Analysis runner: Phase 5 ✓
- §4.5 MCP server: Phase 4 ✓
- §4.6 quix-ai-config: Phase 7 ✓
- §4.7 Frontend AI Summary sub-tab: Phase 6 ✓
- §5 Data flow: covered by runner tests + integration in Phase 5
- §6 MCP contracts: handler tests in Phase 4
- §7 Error handling: timeout/no-save/sse-drop/orphan tests in Phase 5
- §7 Logging: `[auth]` tightening in Phase 1, structured runner logs in Phase 5
- §8 Auth: `update_permission`/`read_permission` in Phase 3, X-API-Key in Phase 4
- §9 Testing + TDD: each task uses red→green→commit
- §10 Rollout: 7 commits map 1:1 to 7 phases
- §11 Future-proofing: `schema_version` + `extra` field in Phase 2

**Placeholder scan:** searched for `TODO`, `TBD`, `FIXME`. Two soft references in Phase 7 noting that exact `mcp__quixlake__*` tool names and the KB-resource API endpoint shape need confirmation at impl time via existing probes — these aren't placeholders for code, they're known impl-time verifications, called out explicitly.

**Type consistency:** Pydantic field names (`tokens_in`, `tokens_out`, `tokens_cache_create`, `tokens_cache_read`, `duration_ms`, `summary_md`, `kpis`, `requirements_check`, `anomalies`, `logbook_refs`, `extra`, `schema_version`) used consistently across Phase 2 schema, Phase 5 runner, Phase 4 MCP write handler, and Phase 6 frontend types.

**Scope check:** ~7 commits, ~1500-1700 LOC total. Each phase is self-contained and shippable on its own. Plan does NOT cover the v2 auto-trigger (per spec §1 deferred list).

