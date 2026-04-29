# Chat Backend Integration into Telemetry Explorer — Design

**Date:** 2026-04-29
**Branch:** `feature/sc-72383/integrate-quix-ai-chat-into-telemetry`
**Scope:** Wire the floating/docked chat panel (slices 1+2 already shipped) to a real backend that talks to the Quix AI QuixLake Querier agent. Replace the hardcoded mock plot plan with live Mode 1 (viz plan, can emit `plot` or `clarify`) + Mode 2 (analysis prose) + Mode 3 (deep-analysis defer) responses.

## Goal

The Telemetry Explorer SPA currently has an AI chat panel whose backend is a mock that returns a hardcoded plot plan. This spec describes how to integrate the existing standalone `telemetry-chat/` service's backend code into the Explorer's own FastAPI service so the chat is a first-class Explorer feature, not a separate deployment.

The integration must keep working when the Explorer is iframed inside Test Manager (the `/analysis` tab). No Test-Manager-specific features in this scope — the chat is lake-only, with no awareness of TM's `Test`, `Driver`, or logbook entries (deferred to a later "AI analysis pipeline" iteration).

## Non-Goals

- **Test-Manager-aware context** (option C from brainstorming) — no `postMessage` from TM to Explorer carrying current Test / requirements / logbook entries.
- **Conversation persistence** — chat history is in-memory per page life; reload starts a fresh agent session.
- **Multi-user auth** — chat is anonymous, same posture as `/api/sessions` and `/api/telemetry`.
- **Decommissioning the standalone `telemetry-chat/` service** — it stays deployed as a fallback / playground; no further development happens there.

---

## Architecture

Single-service integration. Telemetry Explorer's FastAPI gains a new chat module that calls Quix AI directly. No proxy, no second service.

```
Browser (Explorer SPA)
  ↕ JSONL stream
Explorer FastAPI (telemetry-comparison/main.py)
  ├─ /api/sessions          (existing — partition tree)
  ├─ /api/telemetry         (existing — lake fan-out for plotting)
  ├─ /api/track + /api/video (existing routers)
  └─ /api/chat              (NEW — Quix AI agent stream)
       ↓ httpx
       Quix Portal /ai/api/sessions + /messages (SSE)
       ↓ agent uses
       quixlake-mcp ↔ QuixLake (Mode 2 SQL)
```

**No backend lake fan-out for plot mode.** The Quix AI agent emits `{type: "plot", traces: [...], signals: [...], ...}`. The backend forwards that JSON unchanged to the frontend. The frontend's existing `applyPlotPlan` (in `static/modules/ai-plot-glue.js`) drives the existing manual-mode plotting via `/api/telemetry`. Single source of truth for charts; no duplication of telemetry-chat's `_fetch_trace` / `lake.py` / `telemetry.py`.

**Test Manager unchanged.** Explorer is iframed via `NEXT_PUBLIC_TELEMETRY_EXPLORER_URL`. Chat works inside the iframe like any other Explorer feature — same DOM, same backend, same auth. No TM frontend changes.

**Standalone `telemetry-chat/` service status:** keep deployed for now, no further changes; new development happens in Explorer.

---

## Components

### Backend (Python)

Copy-adapted from `telemetry-chat/app/`:

| Module | Source | Purpose |
|---|---|---|
| `chat.py` | `app/plot.py` (adapted) | `POST /api/chat` JSONL streamer. Strip lake fan-out (`_fetch_trace`, `telemetry.py`, `lake.py`). Forward plot plan JSON as the `plot` event. Keep `answer_delta` / `answer_break` / `clarify` / `tool_call_start` / `error` events. |
| `quix_ai.py` | `app/quix_ai.py` (verbatim) | `create_session()`, `stream_message()`. Quix Portal AI client over `httpx`. SSE parser. |
| `plans.py` | `app/plans.py` (verbatim) | Pydantic `PlotPlan` / `ClarifyPlan` / `AgentPlan` discriminated union. Cap + single-track validation. |

Wired into `main.py`:

```python
import chat
app.include_router(chat.router)  # next to track_loader, video_proxy
```

### Config additions in `config.py`

- `QUIX_PORTAL_API` — already present (used for video proxy); reused.
- `QUIX_TOKEN` — Bearer for `/ai/api/...`. New env var.
- `AGENT_ID` — default `d578e2f5-c2b7-461a-90d2-70dfac450fb0` (QuixLake Querier), env-overridable so dev/prod can swap agents.

### Dropped from telemetry-chat (NOT transferred)

- `app/lake.py` — Explorer already has `partition_walker.py` + DuckDB-backed `/api/telemetry`.
- `app/telemetry.py` — duplicates Explorer's `/api/telemetry`.
- `app/channels.py` — Explorer already has `static/channels.json` + `/api/channels`.
- `app/partitions.py` — Explorer has `partition_walker.py`.
- `app/auth.py` — Explorer's chat endpoint is anonymous (matches Explorer's other endpoints).

### Frontend (JS) — full parity with telemetry-chat

| File | Source | Purpose |
|---|---|---|
| `static/modules/chat.js` | `telemetry-chat/static/chat.js` (adapted) | Replace `_MOCK_PLAN` with `fetch('/api/chat')` JSONL reader. Render markdown via `markdown-it`. Status bubble, clarify chips, pre/post-tool break. On `plot` event → `applyPlotPlan(plan)`. |
| `static/modules/markdown.js` | `telemetry-chat/static/markdown.js` | Custom link rule (target=_blank, http/https only), rAF-throttled re-render. |
| `static/vendor/markdown-it.js` | `telemetry-chat/static/vendor/markdown-it.js` | Self-hosted (no CDN) for SRI safety. |
| `static/modules/ai-plot-glue.js` | unchanged | `applyPlotPlan()` already wired. |
| `static/modules/chat-overlay.js` | unchanged | Floating/docked panel from slices 1+2. |

### Tests — adapted from `telemetry-chat/tests/`

- `tests/test_chat.py` (was `test_plot.py`) — JSONL streaming events, plan validation, error mapping. `respx` mocks Quix Portal. ~25 cases.
- `tests/test_plans.py` — Pydantic validation. Verbatim port.
- `static/modules/chat.test.js` — vitest: JSONL reader parses ndjson, dispatches `answer_delta` / `plot` / `clarify` / `error`. ~10 cases.

---

## Data flow

The QuixLake Querier agent system prompt picks one of three modes per turn (see `quix-ai-exploration/kb/agent_system_prompt.md`). Mode 1 has two output shapes (`plot` or `clarify`). Mode 2 is prose with SQL via MCP. Mode 3 is a short defer sentence for things outside SQL's reach (ML / clustering / FFT / etc).

### Mode 1 (Viz plan) — happy path: plot

```
1. User types "plot Ludvik laps 2-3 vs Tomas laps 3-4 ks_nurburgring"
2. Frontend POST /api/chat {message, session_id?}
3. Backend:
   a. If no session_id → POST Quix Portal /ai/api/sessions {agentConfigurationId}
   b. Emit {event: "status", message: "Thinking…", session_id}
   c. POST /messages → SSE stream
   d. Stream text_delta → answer_delta events to frontend
      (hold back 6 chars to detect ```json fence cleanly)
   e. Once ```json fence seen → stop streaming text, accumulate JSON
   f. Parse final JSON via plans.py → PlotPlan (Pydantic, type="plot")
   g. Emit {event: "plot", plan: {...}}
4. Frontend chat.js:
   - answer_delta events → append to current bubble (markdown re-render)
   - plot event → applyPlotPlan(plan) → fills dropdowns, ticks laps,
     activates signal chips, calls window.plot()
   - existing /api/telemetry path renders charts (single source of truth)
```

### Mode 1 (Viz plan) — sub-shape: clarify

The agent emits a clarify object instead of a plot when the user's criteria
match more than one session, span multiple tracks, or exceed the trace cap.

```
1. User: "plot ludvik bmw" (ambiguous — multiple sessions match)
2. Agent emits {type: "clarify", question: "Which session?", options: [...]}
3. Backend: parse via plans.py → ClarifyPlan, emit {event: "clarify", ...}
4. Frontend: render question + option chips. Clicking a chip sends that
   option text as the next chat message; the agent re-runs Mode 1 with
   the disambiguated criteria.
```

### Mode 2 (Analysis)

```
1. User: "fastest lap on bmw_1m at ks_nurburgring?"
2. Backend: same agent session, stream message
3. Agent: "I'll query the lake…" (text_delta) → tool_call_start mcp__...__run_query
4. Backend emits answer_break (split bubble pre/post tool)
5. Agent invokes quixlake-mcp → SQL execution → tool_result
6. Agent: "Tomas's lap 3 at 1:47.600 was fastest." (text_delta)
7. No JSON fence → no plot event → done
8. Frontend: two bubbles in chat (pre + post-tool prose), both markdown-rendered
```

### Mode 3 (Deep-analysis defer)

Triggered when the user asks for ML/clustering/anomaly/FFT/racing-line
optimisation/driving-style analysis — anything beyond plain SQL. Agent
replies with a single short sentence saying it's not supported yet.

```
1. User: "find anomalies in tyre temps across all sessions"
2. Agent (per system prompt): one-line refusal — no tool calls, no SQL,
   no JSON fence
3. Backend: streams text_delta as answer_delta, no plot/clarify event
4. Frontend: single assistant bubble with the refusal text (markdown rendered)
```

From the wire's perspective Mode 3 is identical to a Mode-2 prose answer
with no tool call — backend doesn't need to detect it explicitly.

### Conversation state

Frontend keeps `session_id` in JS module state (per page life). Reload = new session. No localStorage persistence of conversation.

### Auth

Explorer's existing `QUIX_TOKEN` env var is the bearer token for Quix Portal. Frontend → backend is anonymous (same posture as `/api/telemetry`).

### Error handling

Any upstream failure (Portal 5xx, agent timeout, plan validation failure) → `{event: "error", detail, status, session_id?}`. Frontend shows a red error bubble. No server-side retries.

---

## Testing

### Backend (pytest)

| File | Cases |
|---|---|
| `tests/test_chat.py` | JSONL stream: `status` emitted first; `answer_delta` accumulates; fence with `type:"plot"` → `plot` event; fence with `type:"clarify"` → `clarify` event; no fence + no tool call (Mode 3 defer) → just `answer_delta` then done; tool_call_start → `answer_break` event; agent 5xx → `error` event. `respx` mocks `/ai/api/sessions` + `/ai/api/sessions/{id}/messages`. |
| `tests/test_plans.py` | Pydantic validation: `PlotPlan` happy path; `MAX_TRACES` cap; `MAX_SIGNALS` cap; single-track invariant; malformed agent JSON → 502 ; `ClarifyPlan` accepts `options[]`. |

No real Quix Portal calls in CI. Reuse telemetry-chat's `respx` patterns verbatim.

### Frontend (vitest)

`static/modules/chat.test.js` — JSONL reader: parses ndjson chunks; dispatches `answer_delta` to bubble; `plot` event calls `applyPlotPlan`; `clarify` renders option buttons; `error` renders red bubble. Mock `fetch` via `vi.fn`.

### Manual smoke (after wiring)

- **Mode 1 plot:** "plot Ludvik laps 2-3 ks_nurburgring speed throttle" → dropdowns auto-fill, charts render.
- **Mode 1 clarify:** "plot ludvik bmw" → option chips appear; clicking sends as next message; agent re-runs Mode 1 with the chosen option.
- **Mode 2 analysis:** "fastest lap on bmw_1m ks_nurburgring" → prose answer with markdown.
- **Mode 3 defer:** "find anomalies in tyre temps" → short "not supported yet" sentence; no plot, no SQL.
- **Error:** hit endpoint with bogus token → red bubble with status.

### Quality gates

- Backend: `uv run ruff check .`, `uv run ruff format --check .`, `uv run ty check`, `uv run pytest`.
- Frontend: `npm run lint` (chat-overlay slice 2 already has parsing-error noise per global ESLint config — out of scope), `npm run format:check`.

### Don't test

Real Quix Portal calls, real lake queries, agent KB content. All mocked.

---

## Commit cadence

Per repo norms: plain imperative subjects, no Conventional Commits, propose-subject-first, no `--no-verify` / `--amend` / `--push` / `-A`.

Five commits, each self-contained and green:

1. **Add Quix AI client + plan models** — port `quix_ai.py` + `plans.py` from telemetry-chat verbatim. Add `QUIX_TOKEN` + `AGENT_ID` to `config.py`. Tests: `test_plans.py`.
2. **Add /api/chat streaming route** — `chat.py` adapted from `plot.py` minus lake fan-out. Wire into `main.py` via `include_router`. Tests: `test_chat.py` with `respx` mocks.
3. **Replace mock chat backend with real fetch** — rewrite `static/modules/chat.js` to JSONL reader, dispatch events. Drop `_MOCK_PLAN`. `applyPlotPlan` integration unchanged.
4. **Add markdown rendering + chat polish** — copy `static/modules/markdown.js` + `static/vendor/markdown-it.js`. Style status bubble, clarify chips, pre/post-tool break. Frontend test `chat.test.js`.
5. **Manual smoke + docs** — README.md update for `/api/chat` env vars, manual test results captured in PR description.

PR description per repo style: 5 tight bullets, ~100-char lines, no Summary/Why headers, cover the whole branch (slices 1+2+backend).

Branch: extend the existing `feature/sc-72383/integrate-quix-ai-chat-into-telemetry` since slices 1+2 are already there and same SC ticket.

---

## Open questions

None blocking. The following are intentionally deferred:

- TM-aware context (option C). Picked up in a later iteration once the Test record is wired into Explorer (would need iframe `postMessage` from TM carrying `test_id` + requirements + logbook).
- Conversation persistence across reloads. Would require a backend session store + a "history" endpoint. Not requested.
- Multi-user auth on `/api/chat`. Same as Explorer's other endpoints today (anonymous). Revisit when Explorer overall gets per-user auth.
- ESLint parser config rejecting ES module syntax — a global config bug pre-dating this work, called out in slice 1 memory; out of scope.
