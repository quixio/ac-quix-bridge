# Post-Race AI Summary — Design

**Shortcut ticket:** sc-72747
**Branch:** `feature/sc-72747/build-post-race-ai-analyzer-pipeline` (off `feature/test-manager`)
**Date:** 2026-05-21
**Status:** Spec — awaiting plan + implementation

## 1. Goal + scope

Add an AI-generated post-race analysis to Test Manager. For any completed AC racing session, the user can click an Analyze button (or — v2 — let a silence-based trigger fire automatically) and receive a structured + narrative report containing KPIs, requirements check, detected anomalies, driver-logbook echoes, and recommendations. The report is persisted, history-tracked per session, and rendered in a new "AI Summary" sub-tab under the existing Analysis tab.

### In scope (v1)

- Manual trigger via TM frontend (Analyze button + deep-link from Test detail)
- Quix.AI agent ("Post-Race Analyzer") with `delegate_task` enabled for code-exec when needed
- New `test-manager` MCP server hosted inside `test-manager-backend` exposing read tools + a write tool (`save_analysis`)
- New `analyses` Mongo collection + Pydantic models + CRUD routes
- Logbook `session_id` rework (additive field, drift-fix on `LogbookEntryUpdate`, frontend dropdown)
- New AI Summary sub-tab in `app/analysis/`; drop unused sub-tabs (Per-Corner, Live, Single Run, Notebook)
- `quix-ai-config/` repo folder (committed) with system prompt + KBs + setup scripts borrowed from `quixlab/scripts/ai/`
- Async job model: backend asyncio task holds Quix.AI SSE stream silently; frontend polls
- Backend pytest + frontend vitest (new) + Playwright e2e

### Deferred to v2

- Auto trigger via `ac-postrace-trigger` deployment using `StreamTimeoutTracker` on `ac-telemetry-raw`
- Per-test rollup analysis (`session_id: null`)
- Baseline session diff narrative
- `/continue` session resumption for pod-restart resilience
- PDF / HTML export
- Plot specs (`plots: list[PlotSpec]`)
- WebSocket status push (vs polling)
- Portal Frontend PR to render MCP `DisplayName` in tool-call card

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│ TM frontend                                                            │
│   Analysis tab ▸ AI Summary sub-tab                                    │
│     [Test ▾] [Session ▾] [History ▾]   [Analyze ▸]                     │
│     ┌─ Analysis card ────────────────────────────┐                     │
│     │  KPI grid · Reqs pills · Anomalies list    │                     │
│     │  Markdown narrative                        │                     │
│     │  Footer: model · tokens · quix_session_id  │                     │
│     └────────────────────────────────────────────┘                     │
└────────────────────────────────────────────────────────────────────────┘
       │ POST /api/v1/analyses           │ GET /api/v1/analyses/{id}
       │ + Bearer (update_permission)    │ + Bearer (read_permission)
       ▼                                 ▼
┌────────────────────────────────────────────────────────────────────────┐
│ test-manager-backend (FastAPI)                                         │
│                                                                        │
│   ┌─ /api/v1/analyses ────────────┐    ┌─ /mcp ──────────────────────┐ │
│   │ POST   create + spawn task    │    │ Auth: X-API-Key             │ │
│   │ GET    list (filterable)      │    │ Tools (auto-prefixed by     │ │
│   │ GET    /{id} detail + status  │    │  Quix.AI as                 │ │
│   └─────────┬─────────────────────┘    │  mcp__test-manager__*):     │ │
│             │                          │   get_test                  │ │
│             │ asyncio.create_task      │   get_session               │ │
│             ▼                          │   list_logbook              │ │
│   ┌─ analysis_runner ─────────────┐    │   get_driver / device / env │ │
│   │ open Quix.AI SSE              │    │   list_sessions_for_test    │ │
│   │ read events silently          │    │   list_recent_for_driver    │ │
│   │ update status from event log  │    │   save_analysis (write)     │ │
│   │ hold connection for duration  │    └─────────────┬───────────────┘ │
│   └─────────┬─────────────────────┘                  ▲                 │
│             │                                        │                 │
│             ▼                              ┌─────────┴─────────┐       │
│   ┌─ Mongo writes ────────────────┐        │ test_manager db   │       │
│   │ analyses (created, updated)   │───────►│   tests           │       │
│   │ logbook.session_id field      │        │   logbook         │       │
│   └───────────────────────────────┘        │   drivers/devs/.. │       │
│                                            │   analyses ← new  │       │
└────────────────────────────────┬───────────└───────────────────┘───────┘
                                 │
                  ┌──────────────┴───────────────┐
                  ▼                              ▼
        ┌───────────────────┐          ┌─────────────────────┐
        │ Quix.AI agent     │          │ quixlake-mcp        │
        │ "Post-Race ..."   │── HTTPS─►│ SQL on ac_telemetry │
        │ — system prompt   │          └─────────────────────┘
        │ — KBs (3)         │
        │ — toolFilter      │
        │ — calls back to:  │
        │   /mcp (TM)       │
        │   /sql (Quixlake) │
        │   delegate_task   │
        │   (Quix-hosted)   │
        └───────────────────┘
```

User clicks Analyze in TM frontend's AI Summary tab. TM backend creates an `analyses` Mongo doc (`status=pending`), spawns an asyncio task that opens an SSE session against the "Post-Race Analyzer" Quix.AI agent, holds the connection silently, and returns 202. The agent autonomously fetches context by calling read tools on TM backend's `/mcp` subrouter (test + logbook + sessions + cross-ref lookups), queries the lake via `quixlake-mcp` for KPIs and anomalies, optionally spawns a `delegate_task` pod for derivative math, and finally calls `save_analysis` on TM-MCP to persist the structured + Markdown payload. Backend marks status complete. Frontend polls and renders.

## 3. Connection model

**Quix.AI's agent loop is SSE-stream-driven.** Confirmed via probes of `Quix.AI/Quix.AI.Application/Chat/ChatService.cs` and `SessionsController.cs`: the LLM call + tool execution loop runs synchronously inside an `IAsyncEnumerable` consumed by the SSE response stream. Cancellation token from `HttpContext.RequestAborted` flows all the way to `ClaudeLlmProvider.StreamChatAsync()` and `ToolExecutor`. If the consumer disconnects, the agent's work is cancelled mid-step. There is no background-task pattern that continues after disconnect.

Implication: **someone must hold the SSE connection for the agent's entire run.** Three implementation options exist (browser-direct vs backend-bg-task vs job-queue); v1 picks **backend-bg-task** (asyncio in TM backend), v2 may upgrade to job-queue (Celery/Redis) if pod-restart loss becomes painful.

Pod restart mid-analysis → SSE drops → agent cancelled. Mitigation: orphan sweep on app startup marks stuck pending/running docs as `failed`; user re-clicks Retry.

## 4. Components

Six new building blocks + targeted modifications, in dependency order.

### 4.1 Logbook session_id rework

Backend (`api/models.py`, `api/routes/logbook.py`, `api/mongo.py`, `api/routes/tests.py`):

```python
class LogbookEntry(BaseModel):
    id: str = Field(..., alias="_id")
    test_id: str
    session_id: str | None = None        # NEW — None = test-wide note
    created_at: datetime = Field(default_factory=now)
    content: str

class LogbookEntryCreate(BaseModel):
    content: str = Field(..., min_length=1)
    session_id: str | None = None        # NEW

class LogbookEntryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    session_id: str | None = None        # NEW — explicit set/change/clear
    # REMOVED: phantom `timestamp` field (drift bug)
```

- POST validates `session_id` ∈ `test.sessions[]` or null → 400 otherwise
- GET supports `?session_id=` + `?include_test_wide=true`
- Mongo composite index: `[("test_id", 1), ("session_id", 1), ("created_at", -1)]`
- Fix `api/routes/tests.py:288` — change `.sort("timestamp", -1)` → `.sort("created_at", -1)` (already-broken silent ordering bug)
- No backfill migration; existing docs keep `session_id: null` (= test-wide), Pydantic v2 handles missing field

Frontend (`components/tests/logbook-entry-form.tsx`, `logbook-entry-list.tsx`, `types/test.ts`, `lib/api/logbook.ts`):

- Form: session dropdown built from `test.sessions[]` on open
  - Options: "Test-wide" + each session ("2026-05-21 14:32 · barcelona / ferrari_488")
  - Default: latest session by ISO timestamp; "Test-wide" if no sessions
  - Help text under dropdown when empty: "No sessions yet — entry will be test-wide."
- List: session badge per entry ("🏁 14:32" or "Test-wide" pill); filter chips at top
- Comment fix in `logbook-entry-list.tsx:81` (says "sort by timestamp" but actually sorts `created_at`)

### 4.2 Analyses Mongo collection + Pydantic models

`api/models.py`:

```python
class KpiValue(BaseModel):
    name: str                                  # opaque string — agent picks
    value: float | str
    unit: str | None = None
    notes: str | None = None

class RequirementCheck(BaseModel):
    requirement: str
    met: bool | None = None                    # tri-state: true/false/None (undetermined)
    evidence: str | None = None

class Anomaly(BaseModel):
    severity: Literal["info", "warn", "error"]
    kind: str                                  # opaque string
    lap: int | None = None
    time_ms: int | None = None
    description: str
    evidence: str | None = None

class Analysis(BaseModel):
    id: str = Field(..., alias="_id")          # uuid4 string (race-safe across parallel triggers)
    schema_version: int = 1                    # future-proof for v2 reshape
    test_id: str
    session_id: str                            # v1 always set; v2 admits null (rollup)
    status: Literal[
        "pending", "running", "fetching",
        "analyzing", "saving", "complete", "failed",
    ]
    created_at: datetime = Field(default_factory=now)
    updated_at: datetime = Field(default_factory=now)
    quix_session_id: str | None = None
    model: str | None = None                   # e.g. "claude-opus-4-7"
    tokens_in: int | None = None
    tokens_out: int | None = None
    tokens_cache_create: int | None = None     # from Quix.AI UsageEvent — cache write
    tokens_cache_read: int | None = None       # from Quix.AI UsageEvent — cache hit
    duration_ms: int | None = None
    error: str | None = None
    error_kind: Literal["timeout", "agent", "validation", "orphan"] | None = None
    kpis: list[KpiValue] = []
    requirements_check: list[RequirementCheck] = []
    logbook_refs: list[str] = []
    anomalies: list[Anomaly] = []
    summary_md: str = ""                       # required at save time; "" while pending
    extra: dict[str, Any] = {}                 # freeform escape hatch
    model_config = ConfigDict(populate_by_name=True)

class AnalysisCreate(BaseModel):
    test_id: str = Field(..., min_length=1)
    session_id: str = Field(..., min_length=1)

class AnalysisListQuery(PaginationParams):
    test_id: str | None = None
    session_id: str | None = None
    status: Literal["complete", "failed", "in_progress"] | None = None

class SaveAnalysisPayload(BaseModel):
    analysis_id: str
    kpis: list[KpiValue] = []
    requirements_check: list[RequirementCheck] = []
    logbook_refs: list[str] = []
    anomalies: list[Anomaly] = []
    summary_md: str = Field(..., min_length=1)
    extra: dict[str, Any] = {}
```

Indices (`api/mongo.py`):

```python
mongo.analyses.create_index([("test_id", 1), ("session_id", 1), ("created_at", -1)])
mongo.analyses.create_index([("status", 1), ("updated_at", 1)])   # orphan sweep
```

Storage budget: ~5KB per doc typical, ~30KB max. 1000 analyses ≈ 5-30 MB. No retention policy needed.

### 4.3 Analyses routes (`api/routes/analyses.py`, ~150 LOC)

```
POST /api/v1/analyses         → create + spawn task; 202 with {analysis_id}
GET  /api/v1/analyses         → list; supports ?test_id, ?session_id, ?status, pagination
GET  /api/v1/analyses/{id}    → single, includes current status
```

- `POST` validates `session_id ∈ test.sessions[]`; generates `uuid4()` for `_id`; inserts doc; `asyncio.create_task(run_analysis(...))`; returns 202 immediately
- All three gated by Quix Portal `update_permission` / `read_permission` (POST = update; GETs = read)
- ID format: `uuid4` string (race-safe across parallel triggers; matches `LogbookEntry` pattern for "event-shaped" records). Frontend renders a human-friendly label derived from `created_at` + `duration_ms`, e.g. `"Analysis · 2026-05-21 14:32 · 33s"` — internal uuid never shown.

### 4.4 Analysis runner (`api/analysis_runner.py`, ~120 LOC)

```python
async def run_analysis(analysis_id: str, test_id: str, session_id: str) -> None:
    """
    Holds Quix.AI SSE for full duration.

    1. Open session via POST /ai/api/sessions with agentConfigurationId
    2. Send seed message with analysis_id + test_id + session_id
    3. Read SSE events silently, update analysis.status from event types:
       - tool_call_start (read tool)   → "fetching"
       - tool_call_start (lake/delegate) → "analyzing"
       - tool_call_start (save_analysis) → "saving"
       - save_analysis tool_result ok  → noop (MCP write side sets "complete")
       - usage event                   → update model/tokens
       - stream end                    → mark "failed" if status not complete
    4. Hard 5-min timeout via asyncio.wait_for; on timeout mark failed
    5. Startup hook: orphan sweep marks stuck docs failed after 10min
    """
```

- Shared httpx AsyncClient for connection pooling
- All status updates go through one helper to ensure `updated_at` always bumped
- Errors (httpx, validation, timeout) caught and translated to `error_kind` taxonomy

### 4.5 MCP server subrouter

Module layout (`api/routes/mcp/`, ~250 LOC across files per `feedback_module_size`):

```
api/routes/mcp/
├── __init__.py            # mount FastMCP at /mcp; X-API-Key auth dep
├── tools.py               # tool callable dict + _TOOL_TITLES map
├── instrument.py          # _instrument_tool decorator (port from quixlab)
└── handlers/
    ├── core.py            # get_test, get_session, list_logbook
    ├── lookups.py         # get_driver, get_device, get_environment
    ├── history.py         # list_sessions_for_test, list_recent_sessions_for_driver
    └── write.py           # save_analysis
```

Pattern adopted from `quixlab/src/quixlab/server/mcp/server.py`:
- `mcp.tool(name=name, title=_TOOL_TITLES.get(name))(_instrument_tool(name, fn))` registration loop
- `functools.wraps` preserves `__signature__` for FastMCP JSON-schema introspection
- INFO log on each call: name + sorted kwarg keys, duration on exit, level WARN on raise

### 4.6 Quix.AI agent + KBs + setup scripts → `quix-ai-config/`

```
quix-ai-config/
├── README.md
├── scripts/                              # SHARED across agents, committed
│   ├── update_agent.py                   # agent config INLINE + push logic
│   ├── update_kb_resource.py             # adapted from quixlab/scripts/ai/
│   ├── bind_kb_to_agent.py               # adapted
│   ├── register_mcp.py                   # NEW — register MCP server in Quix.AI org
│   ├── list_agents.py                    # adapted (debug helper)
│   └── list_kbs.py                       # adapted (debug helper)
├── post-race/                            # per-agent assets (data, not code)
│   ├── system_prompt.md                  # canonical narrative prompt
│   └── kb/
│       ├── analysis_contract.md          # SaveAnalysisPayload field semantics
│       └── tm_schema.md                  # Test/SessionInfo/LogbookEntry shapes
└── probes/                               # moved from quix-ai-exploration/probes/
    └── (existing probe scripts)
```

**Note on folder safety:** `quix-ai-config/` is NOT a Quix Cloud deployment. Quix Portal only scans deployments listed under the `deployments:` key in top-level `quix.yaml`. Our folder is not listed there, contains no `app.yaml`, and is touched only by hand-run scripts. Zero deployment conflict.

Agent config lives inline in `update_agent.py`, not in a YAML file. One source of truth, no PyYAML dependency, env reads native:

```python
# scripts/update_agent.py
AGENT_CONFIG = {
    "displayName": "Post-Race Analyzer",
    "systemPrompt": Path("post-race/system_prompt.md").read_text(),
    "kbAccessRules": [
        {"knowledgeBaseId": os.environ["AC_TELEMETRY_KB_ID"],     "accessLevel": "read"},
        {"knowledgeBaseId": os.environ["ANALYSIS_CONTRACT_KB_ID"], "accessLevel": "read"},
        {"knowledgeBaseId": os.environ["TM_SCHEMA_KB_ID"],         "accessLevel": "read"},
    ],
    "toolFilter": {
        "mode": "whitelist",
        "toolNames": [
            "delegate_task",                                       # Quix.AI native
            "mcp__quixlake__<tool>",                               # quixlake-mcp tools — exact names TBD via list at impl time
            ...
            "mcp__test-manager__get_test",                         # ours, slug = "test-manager"
            "mcp__test-manager__get_session",
            "mcp__test-manager__list_logbook",
            "mcp__test-manager__get_driver",
            "mcp__test-manager__get_device",
            "mcp__test-manager__get_environment",
            "mcp__test-manager__list_sessions_for_test",
            "mcp__test-manager__list_recent_sessions_for_driver",
            "mcp__test-manager__save_analysis",
        ],
    },
}
```

**Re. `mcp__<serverName>__<toolName>` prefix:** verified via probe of `Quix.AI/Quix.AI.Infrastructure/MCP/McpToolAdapter.cs:64-66`. Quix.AI auto-prefixes MCP tool names when bridging to Claude. The `<serverName>` part = the `Name` property set on the `McpServerConfig` registration (NOT the hex GUID shown in Portal's chat-card UI — that's a separate display gap unrelated to tool naming). For our new MCP server we register `name: "test-manager"` → tools surface as `mcp__test-manager__*`. The `mcp__quixlake__*` names depend on whatever slug the existing `quixlake-mcp` server was registered under in Quix.AI org config; **confirm exact names at impl time via `scripts/list_agents.py` or by inspecting the deployed agent's tool list**, since we never standardised this slug across our probes.

System prompt structure (per memory `feedback_agent_kb_iteration`: hard rules > prose hints, H3 sections > tables for RAG):

- `# Post-Race Analyzer`
- `## Hard rules` — 8 numbered, terse (must-call save_analysis once; lap 1 = out-lap; partition-filter SQL; etc.)
- `## Workflow` — 8-step procedure: read inputs → fetch context → query lake → parse requirements → scan anomalies → delegate_task if needed → compose narrative → save
- `## Output contract` — pointer to KB "Analysis Contract"; reiterates required `summary_md`, optional everything else, loose KPI/anomaly names

Backend seed message to agent — wrapped in HTTP body with workspace context (scopes `delegate_task` pod spawning to our workspace):

```python
body = {
    "message": (
        "Analyze the racing session below.\n\n"
        f"analysis_id: {analysis_id}\n"
        f"test_id:     {test_id}\n"
        f"session_id:  {session_id}\n\n"
        "Workspace context: AC telemetry, lake table = ac_telemetry.\n\n"
        f'Call save_analysis(analysis_id="{analysis_id}", payload={{...}}) exactly once when done.'
    ),
    "context": {
        "workspaceId": os.environ["Quix__Workspace__Id"],
    },
}
```

The `context.workspaceId` is what scopes `delegate_task` to spawn its K8s DevSession pod in our workspace. Required field at message time. `workspaceName` and `page` keys (used by Portal's chat UI when sending) are display-only fluff and omitted from our backend caller.

### 4.7 Frontend AI Summary sub-tab + deep-link

`app/analysis/page.tsx` (modify):
- Register `ai-summary` sub-tab; drop stub sub-tabs (Per-Corner, Live, Single Run, Notebook) — were empty placeholders
- Keep Compare + Leaderboard + new AI Summary

`app/analysis/ai-summary/` (new):
- `page.tsx` — reads `test_id`, `session_id` from URL params; orchestrates pickers + card
- `components/test-session-picker.tsx` — two dropdowns + history selector
- `components/analysis-card.tsx` — KPI grid + reqs pills + anomalies + Markdown
- `components/analyze-button.tsx` — POST + initiate polling
- `hooks/use-analysis-polling.ts` — 3s polling, backoff to 5s after 60s, cap at 100 polls / 5 min, stop on terminal status

`components/tests/test-detail-card.tsx` (modify):
- Add "AI Summary" button alongside "Analyze Telemetry"; per-session button row in Sessions list
- Both use `router.push('/analysis?tab=ai-summary&test_id=...&session_id=...')`

`lib/api/analyses.ts` (new): client methods `create`, `list`, `get`

### Approximate LOC

- Backend new: ~600 LOC (models + routes + MCP + runner)
- Backend modified: ~80 LOC
- Frontend new: ~400 LOC (sub-tab + components + hooks + api client)
- Frontend modified: ~50 LOC (analysis page, test-detail-card, logbook form/list, types)
- `quix-ai-config/`: ~150 LOC (scripts + system prompt + KBs)
- Tests: ~400 LOC (pytest + vitest + Playwright)
- **Total: ~1500-1700 LOC**

Per `feedback_module_size`: largest single file ≤ 300 LOC; MCP split across handler files keeps each in 50-150 range.

## 5. Data flow — happy path

> Note: examples in this section and §7 use `ANA-0042` as a stand-in for analysis IDs. Actual stored `_id` = uuid4 string (per §4.2 schema) — uuid kept out of mockups for readability.


```
[browser]      [tm-backend]            [mongo]        [quix.ai agent]     [tm-backend /mcp]      [quixlake-mcp]
   │
   │ user clicks Analyze on session sess-X
   ▼
   POST /api/v1/analyses { test_id, session_id }
   ───────────────────►│
                       │ validate JWT (update_permission)
                       │ validate session_id ∈ test.sessions[]
                       │ generate ANA-0042
                       │ insert {status:"pending"} ──►│
                       │ asyncio.create_task(run(...))
   ◄── 202 {analysis_id} ─│
                       │ ⏵ task: open Quix.AI SSE (agentConfigurationId)
                       │   ───────────────────────────────►│
                       │   ◄────────────── {quix_session_id}
                       │   update analysis.quix_session_id ──►│
                       │   update status="running"
                       │   send seed message ─────────────────►│
                       │                                       │ tool_call_start get_test
                       │   ◄── tool_call_start
                       │       status="fetching"
                       │                                       ├──────────────────►│
                       │                                       │   (validate X-API-Key,
                       │                                       │    fetch + resolve)
                       │                                       │   ◄───────────────│
                       │   ◄── tool_result …
                       │   ◄── tool_call_start mcp__quixlake__sql
                       │       status="analyzing"
                       │                                       ├──────────────────────────────►│
                       │                                       │   ◄───────────────────────────│
                       │   (optional) ◄── environment_agent_* (delegate_task pod)
                       │   ◄── text_delta "…" (not forwarded)
                       │   ◄── tool_call_start save_analysis
                       │       status="saving"
                       │                                       ├──────────►│ validate payload,
                       │                                       │           │ $set fields,
                       │                                       │           │ status="complete"
                       │                                       │   ◄───────│ {ok:true}
                       │   ◄── tool_result
                       │   ◄── usage {tokens, model} → store
                       │   ◄── stream end → close, task done
                       │
   browser polls every 3s:
   GET /api/v1/analyses/ANA-0042
   ────────────────►│
   ◄── {status:"pending"} →… → {status:"complete", kpis:[…], summary_md:"…"}
   render
```

### Status state machine

```
pending → running → fetching → analyzing → saving → complete
     \                                                ▲
      \                                               │
       └─────► failed (timeout / agent / orphan) ─────┘
```

Backend derives status from SSE event types as listed above.

### Timing budget

| Phase | Target | Hard limit |
|---|---|---|
| Insert + spawn task (HTTP response) | <200 ms | — |
| SSE open + first event | <2 s | 30 s → fail |
| Tool calls (read + SQL) | 5-15 s | — |
| delegate_task (if used) | 10-30 s | — |
| Streaming narrative | 5-15 s | — |
| save_analysis | <500 ms | — |
| **Total** | **30-60 s** | **5 min → fail** |

### Frontend polling cadence

- First poll 1 s after POST (skip initial pending blip)
- 3 s interval while status non-terminal
- Backoff to 5 s after 60 s elapsed
- Cap at 100 polls (~5 min)
- Stop on `complete` or `failed`

### Re-analyze flow

Same as fresh — new ANA-NNNN, appended to history. Frontend history dropdown shows latest pre-selected; user can switch.

### Auto trigger (v2 sketch)

```
ac-postrace-trigger deployment
  └─ StreamTimeoutTracker on ac-telemetry-raw, key=hostname
      └─ silence threshold hit
          → resolve hostname → test_id via DCM target_key lookup
          → resolve latest session_id from test.sessions[]
          → POST /api/v1/analyses {test_id, session_id}, X-Service-Token
          → identical handler → identical pipeline below the trigger
```

## 6. MCP tool contracts

`/mcp` subrouter on TM backend; auth via `X-API-Key`. Server registered in Quix.AI org with `name: "test-manager"`; tools auto-prefixed by Quix.AI to `mcp__test-manager__<tool>` when surfaced to Claude (verified via probe of `Quix.AI/Quix.AI.Infrastructure/MCP/McpToolAdapter.cs:64-66`).

Our server-side names are plain snake_case; titles via `_TOOL_TITLES` map (matches `quixlab/src/quixlab/server/mcp/server.py:47-58` pattern).

### Tool catalog

```
get_test                          read
get_session                       read
list_logbook                      read
get_driver                        read
get_device                        read
get_environment                   read
list_sessions_for_test            read
list_recent_sessions_for_driver   read
save_analysis                     write (privileged)
```

### Signatures (abbreviated)

```
get_test(test_id) → Test (with resolved driver/device/env names)
get_session(test_id, session_id) → SessionInfo
list_logbook(test_id, session_id?, include_test_wide=false) → list[LogbookEntry]
get_driver(id) → Driver
get_device(id) → Device
get_environment(id) → Environment
list_sessions_for_test(test_id) → list[SessionInfo]  (sorted desc by session_id)
list_recent_sessions_for_driver(driver_id, limit=5) → list[{test_id, session_id, …}]
save_analysis(analysis_id, payload: SaveAnalysisPayload) → {ok, analysis_id}
```

### Error envelope

Standard MCP error shape: `{ code, message, data? }`.

- 404 unknown id / test / session
- 422 SaveAnalysisPayload validation failure with field-level errors
- 409 save_analysis on doc already in `complete` status (idempotency guard)
- 401 missing / wrong X-API-Key

### `save_analysis` server behaviour

- Validate against `SaveAnalysisPayload` Pydantic
- Look up doc by `analysis_id`; reject if not in `{running, fetching, analyzing, saving}`
- `$set` content fields + `status="complete"` + `updated_at`
- Reject double-save with 409

## 7. Error handling

| Failure | Detection | Response | Final status |
|---|---|---|---|
| Quix.AI session open fails | httpx exception in task | mark failed, `error_kind="agent"` | failed |
| SSE drops mid-stream | task catches httpx error | mark failed, `error_kind="agent"` | failed |
| Agent > 5 min | `asyncio.wait_for` timeout | cancel task, mark failed, `error_kind="timeout"` | failed |
| Agent never calls `save_analysis` | stream end without save event | mark failed, `error_kind="agent"`, error="no save_analysis call" | failed |
| `save_analysis` payload invalid | Pydantic in MCP handler | 422 to agent with field errors; agent may retry; 5-min timeout backstops | saving → failed |
| `save_analysis` called twice | Status guard in handler | first wins; second gets 409 | complete |
| `delegate_task` pod fails | Agent sees tool_result error | Agent's call (out of our control) | depends |
| `quixlake-mcp` SQL error | Agent sees tool_result error | Agent's call | depends |
| TM-MCP tool 404 | Handler validation | 404 to agent; likely fatal for run | likely failed |
| TM backend pod restart | startup hook orphan sweep | dock older than 10 min with non-terminal status → failed `error_kind="orphan"` | failed |
| Frontend stops polling | N/A | analysis still completes server-side | unaffected |
| Mongo write fails | pymongo exception | log; orphan sweep eventually catches | eventual failed |

### Logging

Existing TM backend uses Python `logging`, root level set via `LOG_LEVEL` env var (default `INFO`) in `test-manager-backend/main.py:14-17`. We reuse this — no new env var.

**Level taxonomy:** INFO = "one line per user action a human would want to see in normal flow". DEBUG = "set when investigating". WARN = "unexpected, look at this". ERROR = "fatal for the operation".

**Per-surface levels:**

| Source | Level | Notes |
|---|---|---|
| `[auth] OK` per request (existing — **changed**) | DEBUG | One line per request = spam in prod; lift to DEBUG when audit needed |
| `[auth] REJECTED` (existing — **changed**) | WARN | Failed auth = security signal worth surfacing |
| `[analyses] POST create` | INFO | Bookend: `analyses ANA-0042 (test=TST-7 session=...)` |
| `[analyses] GET list/detail` | DEBUG | Read-side, polling-heavy |
| `[runner] analysis started` | INFO | One per analysis trigger |
| `[runner] analysis completed in Xs, Y→Z tok` | INFO | One per successful analysis |
| `[runner] analysis failed (kind, reason)` | WARN (timeout/agent) / ERROR (orphan/mongo) | Surfaces problems |
| `[runner] tool_call_start <name>` | DEBUG | 5-10 per analysis × every analysis = chatty |
| `[runner] SSE raw event` | DEBUG | Investigation only |
| `[runner] status transition` | DEBUG | Visible via Mongo doc, logging duplicates |
| MCP `_instrument_tool` entry/exit | DEBUG | Quixlab pattern — DEBUG default, WARN on exception (preserve existing) |
| MCP `[mcp] wrong X-API-Key` | WARN | Include token preview + request origin |

**At default INFO, per analysis ≈ 3 lines:** `POST create` → `runner started` → `runner completed`/`failed`. Tight.

**Token preview helper** (existing `_token_preview` in `api/auth.py:13-19`) — reused in MCP auth handler for "wrong X-API-Key" WARN line. Reveals `first6...last4 (len=N)` — safe debug aid without leaking full secret.

**`api/auth.py` tightening:** flip `[auth] OK` from INFO → DEBUG, `[auth] REJECTED` from INFO → WARN. Small two-line change.

**Sensitive-data redaction rule:** never log `summary_md`, `kpis`, `requirements_check`, `anomalies`, or raw lake row contents at any level. These can carry driver names, test details, telemetry values. Only log presence/lengths (e.g. `summary_md_len=2348`, `kpis_count=6`). MCP `_instrument_tool` already logs only sorted kwarg KEYS, not values — preserve.

### Failure UX

Frontend `failed` state:

```
┌─ Analysis ANA-0042 · failed ──────────────────┐
│  ⚠ Analysis didn't complete                   │
│  Reason: Agent exceeded 5-min budget          │
│  [ Retry ▸ ]   [ View Quix.AI transcript ]    │
└────────────────────────────────────────────────┘
```

`quix_session_id` always stored even on failure → click-through to Quix.AI debug.

### Out of scope (v1)

- Auto-retry on transient failures — user clicks Retry. Avoids cost spiral.
- `/continue` resumption — v2 resilience feature.
- Partial save — agent's contract is single-shot with all populated fields.

## 8. Auth + permissions

| Surface | Auth |
|---|---|
| `POST /api/v1/analyses` | Quix Portal `update_permission` (workspace Developer / Admin role) |
| `GET /api/v1/analyses[/{id}]` | Quix Portal `read_permission` (workspace Viewer suffices) |
| `/mcp/*` tools (read + write) | `X-API-Key` shared secret; key stored in TM backend env + Quix.AI vault for MCP server config |
| `/api/v1/tests/{id}/logbook` | unchanged (existing `read_permission` / `update_permission`) |

- LLM cost protection: only `update_permission` can fire Analyze, blocking Viewers from incurring cost
- MCP server has no portal user identity — the agent is a system caller authenticated by API key
- Origin / IP allowlist on `/mcp` deferred (v2 hardening if abuse appears)

### Env vars (TM backend)

**Auto-injected by Quix Portal (already present in every deployment, no manual config):**

```
Quix__Portal__Api             base URL for Portal API; we append /ai/api/sessions[/{id}/messages]
Quix__Workspace__Id           current workspace ID; passed in session/message context for delegate_task scoping
```

**New, set manually per deployment after one-time setup:**

```
QUIX_AI_POST_RACE_AGENT_ID    filled after running scripts/update_agent.py
TESTMANAGER_MCP_API_KEY       shared secret; also stored in Quix.AI vault for MCP server config
```

Both new vars declared as ProjectVariables in `quix.yaml`; API key flagged `secret: true`.

## 9. Testing

### Backend pytest (testcontainers + Mongo + respx for Quix.AI mock)

`test_logbook.py` (extend existing):
- create with valid session_id → 201
- create with invalid session_id (not in `test.sessions[]`) → 400
- create with null session_id → 201, stored as test-wide
- list filter by session
- list with include_test_wide
- update changes session_id (null↔value)
- update with phantom `timestamp` field → field silently ignored (no phantom column written)
- sort fix verified on TestFullData

`test_analyses.py` (new):
- POST returns 202 with analysis_id and inserts `status=pending` doc
- POST validates session_id exists on test
- list filters (test_id, session_id, status)
- list sort desc
- GET by id, 404 on unknown

`test_mcp_server.py` (new):
- 401 without / wrong X-API-Key
- each read tool happy path
- 404 on unknown ids
- `save_analysis` happy path → status complete
- `save_analysis` invalid payload → 422 with field errors
- `save_analysis` unknown analysis_id → 404
- `save_analysis` double call → 409
- `save_analysis` wrong status → 409

`test_analysis_runner.py` (new, `respx` mocking Quix.AI):
- happy path: SSE with save_analysis tool_call → all status transitions
- timeout: hang → wait_for fires → failed/timeout
- SSE drop: client disconnect → failed/agent
- no save: stream ends without save → failed/agent
- orphan cleanup: old running doc → startup hook marks failed/orphan

### Frontend

**vitest** (NEW for TM frontend; setup mirrors `telemetry-comparison/vitest.config.ts`):
- `use-analysis-polling` hook — state machine, backoff, stop conditions, max-cap
- session-picker default logic — latest by ISO desc; fallback to Test-wide
- `KpiTile`, `RequirementsPills`, `AnomalyCallout` component rendering
- Markdown sanitizer wrapper (strip `<script>`, allow headings/bold)
- history sorter
- Bonus starter set: `logbook-entry-form.tsx` dropdown defaults; `logbook-entry-list.tsx` badge rendering

**Playwright** (extend existing `e2e/`):
- `ai-summary.spec.ts` — sub-tab nav, dropdowns, Analyze flow with mocked backend, failed state, history dropdown grows
- `logbook-session.spec.ts` — dropdown rendering across session-count scenarios; create / edit / filter chips

### Manual cloud verification (pre-merge)

After scripts pushed agent + KBs + MCP registration to Quix.AI org:

1. Deploy preview env (or local stack with public URL exposed for MCP callback)
2. Open TM frontend → AI Summary sub-tab → pick real test+session that has lake data
3. Click Analyze → watch status transitions
4. After 30-60 s, see complete analysis: KPI grid + reqs pills + anomalies + Markdown
5. "View Quix.AI transcript" link → confirm tool calls + reasoning visible in Quix.AI chat view
6. Re-click → second history entry appears
7. Mongo: `db.analyses.find()` → two complete docs
8. Force failure (kill backend during analysis) → `failed` state + Retry works

### Implementation discipline: TDD

Implementation follows `superpowers:test-driven-development`. For every increment:

1. Write the failing test FIRST (red)
2. Write minimal impl to pass (green)
3. Refactor
4. Commit BOTH test + impl together

Tests are not "added at the end" — they ship with each commit. Each commit is independently shippable and tested. Per `feedback_delegate_verification`, the reset-reseed-run-debug cycle is delegated to the `test-runner` subagent during impl to keep main-thread context clean.

### Gates — tiered

Run gates at three checkpoints. Faster gates per-commit, full suite pre-push, manual cloud pre-merge.

**Per commit** (per `feedback_run_full_gates` + `feedback_lint_format_order`):

| Touched | Gates |
|---|---|
| Backend Python | `uv run ruff check .` + `uv run ruff format --check .` + `uv run ty check` + `uv run pytest <focused test file(s) for this increment>` |
| Frontend TS/React | `npm run lint` + `npm run type-check` + `npm run test -- <focused spec>` |
| Both | Both sets |
| Doc-only | Skip |

Focused pytest/vitest = the tests written for this commit's increment, not the whole suite. Cross-commit interactions caught at the pre-push gate below.

**Pre-push** (full sweep, catches cross-commit + prod-only issues):

- Backend: `uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
- Frontend: `npm run lint && npm run type-check && npm run build && npm run test && npm run test:e2e`
- `next build` (not just `dev`) — catches Radix/StrictMode prod-only races per `feedback_verify_before_push`

**Pre-merge:**

- All pre-push gates green
- Manual cloud round above (real LLM + lake + Quix.AI agent)

## 10. Rollout

### Commit slicing (single branch, logical commits)

```
1. Add session_id to logbook entries, fix sort drift
2. Add analyses model and Mongo collection
3. Add analyses CRUD routes
4. Add test-manager MCP server with read tools and save_analysis
5. Add analysis runner with Quix.AI SSE consumer
6. Add AI Summary sub-tab and analyses frontend
7. Add quix-ai-config scripts and post-race agent assets
```

Each commit shippable in isolation. PR description summarises whole branch in ~5 tight bullets (per `feedback_pr_description_style`).

### Setup runbook (one-time, in order)

```bash
cd quix-ai-config
python scripts/register_mcp.py \
    --name test-manager \
    --url https://test-manager-backend-<project>.<env>.quix.io/mcp \
    --api-key $(openssl rand -hex 32)
# → writes server_id to .env

python scripts/update_kb_resource.py post-race/kb/analysis_contract.md
python scripts/update_kb_resource.py post-race/kb/tm_schema.md
# → writes KB IDs to .env

python scripts/update_agent.py post-race/agent.yaml
# → writes agent_id to .env

# Then in Quix Portal UI on test-manager-backend deployment, set:
#   TESTMANAGER_MCP_API_KEY        = (the key from step 1)
#   QUIX_AI_POST_RACE_AGENT_ID     = (the id from step 3)
# Redeploy TM backend.
```

### Pre-merge checklist

- [ ] All backend gates green
- [ ] All frontend gates green
- [ ] vitest run green
- [ ] Playwright run green
- [ ] Manual cloud round passes
- [ ] System prompt + KB content reviewed (no leaked secrets, no test-specific values)
- [ ] `register_mcp.py` + `update_agent.py` ran successfully
- [ ] TM backend env vars set
- [ ] Quix.AI vault has TESTMANAGER_MCP_API_KEY for MCP server config
- [ ] PR description = ~5 tight bullets, ~100-char lines

### Rollout risk

Small blast radius:
- Logbook session_id is additive, null default → existing entries unaffected
- Drift fix on logbook sort: tiny ordering change, no data touched
- Analyses routes / MCP / runner are brand-new surface, can't break existing flows
- Dropped sub-tabs were empty stubs

### Rollback

Standard Quix Portal deployment revert; no DB migration to undo. Mongo docs lying around (analyses, session_id on logbook) are forward-compatible with old code (Pydantic v2 ignores unknown fields).

### Cost

- Per analysis: ~$0.05-0.20 Anthropic (Quix.AI billing)
- `delegate_task` pod when used: extra ~$0.01 per minute
- Workspace-internal use, low volume → negligible
- `update_permission` gate blocks Viewers from triggering

## 11. Future-proofing decisions baked in v1

| Choice | Rationale |
|---|---|
| `schema_version: int = 1` field | Frontend can branch on v2 reshape without backfill |
| `extra: dict[str, Any] = {}` on Analysis | Agent never blocked by schema gap; promote frequently-used keys later |
| Loose opaque strings for `kpis[].name`, `anomalies[].kind` | No enum lock-in; renames don't require migration |
| `session_id: str` on Analysis (v1 always set) but model admits null | v2 per-test rollup needs no schema change |
| Single `logbook` collection with `session_id` FK | No fragmentation between global / per-session storage |
| Backend asyncio task holds SSE | Plan-B job queue (Celery) can be retrofitted later if pod restart pain shows up |
| Agent config + KBs + scripts all committed in `quix-ai-config/` | Reproducible setup, diffable system prompt |

## 12. Out of scope explicitly

- Real-time progress prose streamed to browser (only status updates; per Section 3, async job model)
- AC source / lake / session-config-bridge / DCM behaviour (unchanged)
- Per-test rollup analysis UI
- Auto-trigger via silence detection
- delegate_task usage policy beyond toolFilter allowlist + system-prompt guideline
- LLM prompt iteration / output quality tuning (post-merge work)
- PDF / HTML export
- Embedded plots in analyses
- MCP server displayName rendering in Quix Portal chat card (separate Portal Frontend PR)

## 13. Open questions for plan phase

- Concrete schema for `KpiTile` grid columns (group by category? alphabetical? agent-ordered?)
- Polling implementation — `useEffect` + `setInterval`, or React Query's polling, or SWR? (Pick during plan)
- `_TOOL_TITLES` exact strings — minor; finalise during plan from quixlab pattern
- Should the `register_mcp.py` script idempotently update an existing `test-manager` entry, or fail loudly on conflict? (Lean idempotent, but confirm)
