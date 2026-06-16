# Chat Backend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mock chat backend in Telemetry Explorer with a real `POST /api/chat` endpoint that streams Quix AI agent responses (Mode 1 plot/clarify, Mode 2 analysis prose, Mode 3 defer) and ports the markdown-rendering chat polish from the standalone `telemetry-chat/` service.

**Architecture:** Single FastAPI service (no proxy, no second deployment). Three new Python modules in `telemetry-comparison/` (`quix_ai.py`, `plans.py`, `chat.py`) wire `/api/chat` JSONL streaming. Frontend `static/modules/chat.js` rewrites the mock into a JSONL reader that dispatches `status` / `answer_delta` / `answer_break` / `clarify` / `plot` / `error` events; the existing `applyPlotPlan` in `ai-plot-glue.js` continues to drive the plot UI. Markdown rendering ports verbatim (`markdown.js` + self-hosted `vendor/markdown-it.js`).

**Tech Stack:** FastAPI, httpx (async + SSE streaming), Pydantic v2 (discriminated union for `AgentPlan`), respx (mock Quix Portal in tests), pytest, vanilla JS modules, markdown-it, vitest.

**Source spec:** `docs/superpowers/specs/2026-04-29-chat-backend-integration-design.md`

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `telemetry-comparison/config.py` | Modify | Add `QUIX_TOKEN`, `AGENT_CONFIGURATION_ID`, `portal_headers()` helper. |
| `telemetry-comparison/quix_ai.py` | Create | `httpx`-based Quix AI client (`create_session`, `stream_message`). |
| `telemetry-comparison/plans.py` | Create | Pydantic models (`Trace`, `PlotPlan`, `ClarifyPlan`, `AgentPlan` discriminated union). |
| `telemetry-comparison/chat.py` | Create | `POST /api/chat` JSONL streamer (adapted from `telemetry-chat/app/plot.py` minus lake fan-out). |
| `telemetry-comparison/main.py` | Modify | `app.include_router(chat.router)`. |
| `telemetry-comparison/tests/test_plans.py` | Create | Pydantic validation cases. |
| `telemetry-comparison/tests/test_chat.py` | Create | JSONL streaming events with `respx` mocks. |
| `telemetry-comparison/static/modules/markdown.js` | Create | markdown-it wrapper (custom link rule). Verbatim port. |
| `telemetry-comparison/static/vendor/markdown-it.js` | Create (binary copy) | Self-hosted bundle (no CDN, SRI safety). |
| `telemetry-comparison/static/modules/chat.js` | Rewrite | Replace mock with JSONL reader + event dispatcher. Calls `applyPlotPlan` on `plot` event. |
| `telemetry-comparison/static/modules/chat.test.js` | Create | vitest: JSONL reader, event dispatch. |
| `telemetry-comparison/static/styles.css` | Modify | Status spinner, clarify chips, error bubble, pre/post-tool break. |
| `telemetry-comparison/static/index.html` | Modify | Add `<script type="importmap">` for markdown-it self-host. |
| `telemetry-comparison/README.md` | Modify | Document `QUIX_TOKEN` + `QUIX_AI_AGENT_ID` env vars. |

All Python module paths are flat (no `app/` package). Same layout as existing `track_loader.py` / `video_proxy.py` / `partition_walker.py`.

---

## Task 1: Add Quix AI config to `config.py`

**Files:**
- Modify: `telemetry-comparison/config.py`

- [ ] **Step 1: Add env vars + portal_headers helper**

Append to `telemetry-comparison/config.py` (after the existing `BLOB_VIDEO_PREFIX` line):

```python
# Quix Portal API base. Auto-injected as `Quix__Portal__Api` in Quix Cloud;
# falls back to `QUIX_PORTAL_API` for local dev.
PORTAL = (
    os.getenv("Quix__Portal__Api") or os.getenv("QUIX_PORTAL_API") or ""
).rstrip("/")
QUIX_TOKEN = os.getenv("QUIX_TOKEN", "")

# QuixLake Querier agent (system prompt + KBs + MCP tools live on it).
# Override via env if you need to point at a fork of the agent.
AGENT_CONFIGURATION_ID = os.getenv(
    "QUIX_AI_AGENT_ID", "d578e2f5-c2b7-461a-90d2-70dfac450fb0"
)


def portal_headers(*, streaming: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {QUIX_TOKEN}",
        "Content-Type": "application/json",
    }
    if streaming:
        headers["Accept"] = "text/event-stream"
    return headers
```

- [ ] **Step 2: Verify import works**

Run: `cd telemetry-comparison && uv run python -c "import config; print(config.PORTAL, config.AGENT_CONFIGURATION_ID)"`
Expected: prints empty string + agent UUID (no traceback).

- [ ] **Step 3: Commit**

```bash
git add telemetry-comparison/config.py
git commit -m "Add Quix AI portal config + headers helper"
```

---

## Task 2: Add plan models (`plans.py`) — TDD

**Files:**
- Create: `telemetry-comparison/plans.py`
- Create: `telemetry-comparison/tests/test_plans.py`

- [ ] **Step 1: Write the failing tests**

Create `telemetry-comparison/tests/test_plans.py`:

```python
"""Pydantic plan validation."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from plans import AgentPlan, ClarifyPlan, PlotPlan

ADAPTER: TypeAdapter[AgentPlan] = TypeAdapter(AgentPlan)


def _trace(**overrides: str | int) -> dict:
    base = {
        "session_id": "2026-04-17T06:39:45.652Z",
        "lap": 1,
        "driver": "ludvik",
        "carModel": "bmw_1m",
        "track": "ks_nurburgring",
        "experiment": "VideoSyncFix",
        "environment": "prague_office",
        "test_rig": "g29",
    }
    base.update(overrides)
    return base


def test_plot_plan_minimal_valid() -> None:
    plan = ADAPTER.validate_python(
        {"type": "plot", "signals": ["speedKmh"], "traces": [_trace()]}
    )
    assert isinstance(plan, PlotPlan)
    assert plan.signals == ["speedKmh"]
    assert plan.traces[0].lap == 1


def test_plot_plan_title_optional() -> None:
    plan = ADAPTER.validate_python(
        {"type": "plot", "signals": ["speedKmh"], "traces": [_trace()]}
    )
    assert isinstance(plan, PlotPlan)
    assert plan.title == ""


def test_plot_plan_rejects_empty_signals() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {"type": "plot", "signals": [], "traces": [_trace()]}
        )


def test_plot_plan_rejects_empty_traces() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {"type": "plot", "signals": ["speedKmh"], "traces": []}
        )


def test_trace_ignores_extra_fields() -> None:
    """Trace allows agent to grow optional annotation fields without breaking."""
    plan = ADAPTER.validate_python(
        {
            "type": "plot",
            "signals": ["speedKmh"],
            "traces": [_trace(color_hint="#ff0000")],
        }
    )
    assert isinstance(plan, PlotPlan)


def test_clarify_plan_valid() -> None:
    plan = ADAPTER.validate_python(
        {"type": "clarify", "question": "Which driver?", "options": ["a", "b"]}
    )
    assert isinstance(plan, ClarifyPlan)
    assert plan.options == ["a", "b"]


def test_clarify_plan_options_default_empty() -> None:
    plan = ADAPTER.validate_python(
        {"type": "clarify", "question": "Which driver?"}
    )
    assert isinstance(plan, ClarifyPlan)
    assert plan.options == []


def test_discriminator_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "wat", "signals": ["x"], "traces": [_trace()]})


def test_plot_plan_rejects_extra_fields() -> None:
    """PlotPlan uses extra='forbid' so typos surface immediately."""
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {
                "type": "plot",
                "signals": ["speedKmh"],
                "traces": [_trace()],
                "bogus": "field",
            }
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd telemetry-comparison && uv run pytest tests/test_plans.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plans'`.

- [ ] **Step 3: Create `plans.py`**

Create `telemetry-comparison/plans.py`:

```python
"""Pydantic models for the QuixLake Querier agent's structured plan output.

Standalone — no project-local imports — so the file is portable and the
contract is shared verbatim with the standalone telemetry-chat service.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Trace(BaseModel):
    # `extra="ignore"` on Trace only — leaves room for the agent contract to
    # grow optional annotation fields (e.g. color_hint) without breaking
    # already-deployed services.
    model_config = ConfigDict(extra="ignore")

    session_id: str
    lap: int
    driver: str
    carModel: str
    track: str
    experiment: str
    environment: str
    test_rig: str


class PlotPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["plot"]
    title: str = ""
    signals: list[str] = Field(min_length=1)
    traces: list[Trace] = Field(min_length=1)


class ClarifyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["clarify"]
    question: str
    options: list[str] = []


AgentPlan = Annotated[PlotPlan | ClarifyPlan, Field(discriminator="type")]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd telemetry-comparison && uv run pytest tests/test_plans.py -v`
Expected: 9 passed.

- [ ] **Step 5: Run quality gates**

Run: `cd telemetry-comparison && uv run ruff check plans.py tests/test_plans.py && uv run ruff format --check plans.py tests/test_plans.py && uv run ty check plans.py tests/test_plans.py`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add telemetry-comparison/plans.py telemetry-comparison/tests/test_plans.py
git commit -m "Add Pydantic plan models for Quix AI agent output"
```

---

## Task 3: Add Quix AI client (`quix_ai.py`)

**Files:**
- Create: `telemetry-comparison/quix_ai.py`

This module is small, async, and exclusively wraps two HTTP calls. Tests of its behaviour live inside `test_chat.py` (Task 4) where they mock the Portal endpoints — testing this module in isolation would just re-test `httpx`. So no dedicated test file.

- [ ] **Step 1: Create `quix_ai.py`**

Create `telemetry-comparison/quix_ai.py`:

```python
"""Minimal Quix AI chat client bound to a configured agent.

Sessions are created against `config.AGENT_CONFIGURATION_ID` so the
QuixLake Querier agent's system prompt + knowledge bases + MCP tools are
in scope from turn 1. The backend therefore sends only the raw user
message — no inline instructions, no sessions list, no channels dump.

    POST /ai/api/sessions
        body={"agentConfigurationId": <id>}              -> 200 {id, ...}
    POST /ai/api/sessions/{id}/messages
        body={"message": "...", "context": {}}           -> 200 SSE stream
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx

import config

logger = logging.getLogger(__name__)


async def create_session(client: httpx.AsyncClient) -> str:
    """Open a Quix AI session bound to the QuixLake Querier agent.

    Returns the session UUID.
    """
    r = await client.post(
        f"{config.PORTAL}/ai/api/sessions",
        headers=config.portal_headers(),
        json={"agentConfigurationId": config.AGENT_CONFIGURATION_ID},
    )
    r.raise_for_status()
    data = r.json()
    session_id = data.get("id") or data["sessionId"]
    logger.info(
        "quix_ai: opened session %s (agent=%s)",
        session_id,
        config.AGENT_CONFIGURATION_ID,
    )
    return session_id


async def stream_message(
    client: httpx.AsyncClient, session_id: str, message: str
) -> AsyncIterator[dict]:
    """POST a user message, yield parsed SSE event dicts.

    Filters out `data: [DONE]` sentinels and non-JSON keep-alive lines.
    Yields raw event dicts so the caller decides which to forward, buffer,
    or ignore (e.g. `text_delta` for streaming back, `usage` for logging).
    """
    url = f"{config.PORTAL}/ai/api/sessions/{session_id}/messages"
    body = {"message": message, "context": {}}
    logger.debug("quix_ai: POST %s (%d chars message)", url, len(message))
    async with client.stream(
        "POST", url, headers=config.portal_headers(streaming=True), json=body
    ) as r:
        if r.status_code != 200:
            err_body = await r.aread()
            logger.warning(
                "quix_ai: upstream %d on message POST — body: %s",
                r.status_code,
                err_body[:1000].decode("utf-8", errors="replace"),
            )
            yield {"type": "error", "status": r.status_code}
            return
        async for line in r.aiter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[6:]
            if payload == "[DONE]":
                logger.debug("quix_ai: stream [DONE]")
                return
            try:
                evt = json.loads(payload)
            except json.JSONDecodeError:
                continue
            logger.debug("quix_ai event: %s", _short(evt))
            yield evt


def _short(evt: dict, limit: int = 200) -> str:
    """Compact one Quix AI SSE event into a single-line log string.

    `text_delta` events appear many times per turn; keep them short so
    DEBUG logs stay scannable. Other event types log verbatim.
    """
    if evt.get("type") == "text_delta":
        text = evt.get("text", "")
        return f"text_delta: {text[:limit]!r}{'…' if len(text) > limit else ''}"
    return json.dumps(evt)[:500]
```

- [ ] **Step 2: Verify import works**

Run: `cd telemetry-comparison && uv run python -c "import quix_ai; print(quix_ai.create_session, quix_ai.stream_message)"`
Expected: prints two function objects (no traceback).

- [ ] **Step 3: Run quality gates**

Run: `cd telemetry-comparison && uv run ruff check quix_ai.py && uv run ruff format --check quix_ai.py && uv run ty check quix_ai.py`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add telemetry-comparison/quix_ai.py
git commit -m "Add httpx-based Quix AI client for agent sessions + SSE"
```

---

## Task 4: Add chat streaming route (`chat.py`) — TDD

**Files:**
- Create: `telemetry-comparison/chat.py`
- Create: `telemetry-comparison/tests/test_chat.py`

- [ ] **Step 1: Add respx to dev deps if missing**

Run: `cd telemetry-comparison && uv add --dev respx`
Expected: `pyproject.toml` and `uv.lock` updated.

- [ ] **Step 2: Write the failing tests**

Create `telemetry-comparison/tests/test_chat.py`:

```python
"""POST /api/chat — JSONL streaming integration."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import config
from main import app


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the client at a deterministic Portal URL + token."""
    monkeypatch.setattr(config, "PORTAL", "https://portal.test")
    monkeypatch.setattr(config, "QUIX_TOKEN", "test-token")
    monkeypatch.setattr(config, "AGENT_CONFIGURATION_ID", "agent-uuid")


def _sse(events: list[dict[str, Any]]) -> bytes:
    """Format a list of dicts as the SSE payload Quix Portal returns."""
    out = []
    for evt in events:
        out.append(f"data: {json.dumps(evt)}".encode())
    out.append(b"data: [DONE]")
    return b"\n".join(out) + b"\n"


def _read_jsonl(body: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in body.splitlines() if line.strip()]


@respx.mock
def test_plot_mode_emits_status_and_plot_events() -> None:
    respx.post("https://portal.test/ai/api/sessions").respond(
        200, json={"id": "sess-1"}
    )
    plot_json = {
        "type": "plot",
        "title": "Ludvik lap 1",
        "signals": ["speedKmh"],
        "traces": [
            {
                "session_id": "2026-04-17T06:39:45.652Z",
                "lap": 1,
                "driver": "ludvik",
                "carModel": "bmw_1m",
                "track": "ks_nurburgring",
                "experiment": "VideoSyncFix",
                "environment": "prague_office",
                "test_rig": "g29",
            }
        ],
    }
    sse_body = _sse(
        [
            {"type": "text_delta", "text": "Plotting Ludvik. "},
            {
                "type": "text_delta",
                "text": f"```json\n{json.dumps(plot_json)}\n```",
            },
        ]
    )
    respx.post("https://portal.test/ai/api/sessions/sess-1/messages").respond(
        200, content=sse_body
    )

    with TestClient(app) as client:
        r = client.post("/api/chat", json={"message": "plot ludvik"})
        assert r.status_code == 200
        events = _read_jsonl(r.content)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "status"
    assert "plot" in kinds
    plot = next(e for e in events if e["event"] == "plot")
    assert plot["plan"]["type"] == "plot"
    assert plot["plan"]["title"] == "Ludvik lap 1"
    assert plot["plan"]["signals"] == ["speedKmh"]
    assert plot["plan"]["traces"][0]["driver"] == "ludvik"


@respx.mock
def test_clarify_mode_emits_clarify_event() -> None:
    respx.post("https://portal.test/ai/api/sessions").respond(
        200, json={"id": "sess-2"}
    )
    clarify_json = {
        "type": "clarify",
        "question": "Which session?",
        "options": ["a", "b"],
    }
    sse_body = _sse(
        [
            {
                "type": "text_delta",
                "text": f"```json\n{json.dumps(clarify_json)}\n```",
            },
        ]
    )
    respx.post("https://portal.test/ai/api/sessions/sess-2/messages").respond(
        200, content=sse_body
    )

    with TestClient(app) as client:
        events = _read_jsonl(
            client.post("/api/chat", json={"message": "plot ludvik"}).content
        )

    clarify = next(e for e in events if e["event"] == "clarify")
    assert clarify["question"] == "Which session?"
    assert clarify["options"] == ["a", "b"]


@respx.mock
def test_analysis_mode_streams_answer_delta_only() -> None:
    """Mode 2 / Mode 3 — no JSON fence, just prose. Stream answer_delta, no plot."""
    respx.post("https://portal.test/ai/api/sessions").respond(
        200, json={"id": "sess-3"}
    )
    sse_body = _sse(
        [
            {"type": "text_delta", "text": "Tomas's lap "},
            {"type": "text_delta", "text": "3 was fastest."},
        ]
    )
    respx.post("https://portal.test/ai/api/sessions/sess-3/messages").respond(
        200, content=sse_body
    )

    with TestClient(app) as client:
        events = _read_jsonl(
            client.post("/api/chat", json={"message": "fastest lap?"}).content
        )

    kinds = [e["event"] for e in events]
    assert "plot" not in kinds
    assert "clarify" not in kinds
    deltas = [e for e in events if e["event"] == "answer_delta"]
    assert "".join(d["text"] for d in deltas) == "Tomas's lap 3 was fastest."


@respx.mock
def test_tool_call_emits_answer_break() -> None:
    """Mode 2 with tool use — answer_break splits pre/post-tool prose."""
    respx.post("https://portal.test/ai/api/sessions").respond(
        200, json={"id": "sess-4"}
    )
    sse_body = _sse(
        [
            {"type": "text_delta", "text": "Querying lake. "},
            {"type": "tool_call_start", "toolName": "mcp__abc__run_query"},
            {"type": "tool_result", "result": "csv,here"},
            {"type": "text_delta", "text": "Tomas was fastest."},
        ]
    )
    respx.post("https://portal.test/ai/api/sessions/sess-4/messages").respond(
        200, content=sse_body
    )

    with TestClient(app) as client:
        events = _read_jsonl(
            client.post("/api/chat", json={"message": "fastest lap?"}).content
        )

    kinds = [e["event"] for e in events]
    assert "answer_break" in kinds


@respx.mock
def test_agent_5xx_emits_error_event() -> None:
    respx.post("https://portal.test/ai/api/sessions").respond(
        200, json={"id": "sess-5"}
    )
    respx.post("https://portal.test/ai/api/sessions/sess-5/messages").respond(
        503, text="upstream unavailable"
    )

    with TestClient(app) as client:
        events = _read_jsonl(
            client.post("/api/chat", json={"message": "x"}).content
        )

    err = next(e for e in events if e["event"] == "error")
    assert err["status"] == 502  # we re-classify Quix Portal 503 as our 502


@respx.mock
def test_session_id_reused_when_provided() -> None:
    """Frontend sends session_id on follow-up turns; backend skips create."""
    create_route = respx.post("https://portal.test/ai/api/sessions").respond(
        200, json={"id": "should-not-be-called"}
    )
    sse_body = _sse([{"type": "text_delta", "text": "ok"}])
    respx.post(
        "https://portal.test/ai/api/sessions/existing-sess/messages"
    ).respond(200, content=sse_body)

    with TestClient(app) as client:
        client.post(
            "/api/chat",
            json={"message": "follow up", "session_id": "existing-sess"},
        )

    assert create_route.call_count == 0


def test_message_validation_rejects_empty() -> None:
    with TestClient(app) as client:
        r = client.post("/api/chat", json={"message": ""})
        assert r.status_code == 422


def test_message_validation_rejects_oversized() -> None:
    with TestClient(app) as client:
        r = client.post("/api/chat", json={"message": "x" * 2001})
        assert r.status_code == 422


def test_session_id_validation() -> None:
    with TestClient(app) as client:
        r = client.post(
            "/api/chat", json={"message": "x", "session_id": "bad id with spaces"}
        )
        assert r.status_code == 422
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd telemetry-comparison && uv run pytest tests/test_chat.py -v`
Expected: collection error or all 9 tests fail with `404` / `ImportError` (no `chat` module yet).

- [ ] **Step 4: Create `chat.py`**

Create `telemetry-comparison/chat.py`:

```python
"""POST /api/chat — JSONL stream forwarding Quix AI agent output.

The Quix AI agent (QuixLake Querier) returns three flavours of reply:

    Mode 1 (Viz plan): prose + ```json {type: "plot" | "clarify"} ```
    Mode 2 (Analysis): prose only, may include MCP tool_call_start frames
    Mode 3 (Defer)   : short prose refusal, no tool calls, no JSON

We forward all of it to the browser as ndjson with these event shapes:

    {event: "status",       message: str, session_id?: str}
    {event: "answer_delta", session_id: str, text: str}
    {event: "answer_break", session_id: str}
    {event: "clarify",      session_id: str, question: str, options: list[str]}
    {event: "plot",         session_id: str, plan: dict}
    {event: "error",        session_id?: str, detail: str, status: int}

No backend lake fan-out — Explorer's existing /api/telemetry path renders
charts. We pass the raw plot plan through; the frontend's applyPlotPlan
drives the manual UI surfaces.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError, field_validator

from plans import AgentPlan, ClarifyPlan, PlotPlan
from quix_ai import create_session, stream_message

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
JSON_FENCE_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)
FENCE_LOOKAHEAD = 6  # len("```json") - 1, kept back so a split fence never leaks


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not SESSION_ID_RE.fullmatch(v):
            raise ValueError("session_id must be 8-64 chars of [A-Za-z0-9_-]")
        return v


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        _chat_events(req),
        media_type="application/x-ndjson",
        # Disable proxy/nginx buffering so the browser sees events live.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_PLAN_ADAPTER: TypeAdapter[AgentPlan] = TypeAdapter(AgentPlan)


def _event(obj: dict[str, Any]) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def _error_event(session_id: str | None, detail: str, status: int = 502) -> bytes:
    logger.warning("chat error: %s (status=%d, session=%s)", detail, status, session_id)
    return _event(
        {
            "event": "error",
            "session_id": session_id,
            "detail": detail,
            "status": status,
        }
    )


async def _chat_events(req: ChatRequest) -> AsyncIterator[bytes]:
    t_start = time.monotonic()
    logger.info(
        "chat start: msg=%r session=%s",
        req.message[:80],
        req.session_id or "<new>",
    )

    async with httpx.AsyncClient(timeout=120.0) as client:
        session_id = req.session_id
        if session_id is None:
            try:
                session_id = await create_session(client)
            except httpx.HTTPError as e:
                yield _error_event(None, f"Could not open Quix AI session: {e}")
                return

        yield _event(
            {
                "event": "status",
                "message": "Thinking…",
                "session_id": session_id,
            }
        )

        # Hold back the trailing FENCE_LOOKAHEAD chars of unstreamed accum so
        # a fence opener split across deltas (`"```"` then `"json..."`) never
        # leaks visibly to the browser.
        accum = ""
        streamed = 0
        json_seen = False

        async for evt in stream_message(client, session_id, req.message):
            t = evt.get("type")
            if t == "error":
                yield _error_event(session_id, f"upstream {evt.get('status')}")
                return
            if t == "tool_call_start" and not json_seen:
                # Flush any held tail of pre-tool prose, then break the bubble
                # so post-tool prose lands in a fresh assistant message.
                if streamed < len(accum):
                    yield _event(
                        {
                            "event": "answer_delta",
                            "session_id": session_id,
                            "text": accum[streamed:],
                        }
                    )
                    streamed = len(accum)
                yield _event({"event": "answer_break", "session_id": session_id})
                continue
            if t != "text_delta":
                continue
            accum += evt.get("text", "")
            if json_seen:
                continue
            fence_idx = accum.find("```json")
            if fence_idx >= 0:
                end = fence_idx
                json_seen = True
            else:
                end = max(streamed, len(accum) - FENCE_LOOKAHEAD)
            chunk = accum[streamed:end]
            if chunk:
                yield _event(
                    {
                        "event": "answer_delta",
                        "session_id": session_id,
                        "text": chunk,
                    }
                )
                streamed = end

        # Stream any tail bytes we held back when no fence ever appeared
        # (Mode 2 / Mode 3 prose answers).
        if not json_seen and streamed < len(accum):
            yield _event(
                {
                    "event": "answer_delta",
                    "session_id": session_id,
                    "text": accum[streamed:],
                }
            )

    reply = accum
    if not reply.strip():
        yield _error_event(session_id, "Agent returned an empty reply")
        return

    # No fence -> Mode 2 / Mode 3 prose answer; everything already streamed.
    if not JSON_FENCE_RE.search(reply):
        logger.info("chat answer (no JSON) in %.1fs", time.monotonic() - t_start)
        return

    try:
        parsed = _extract_json(reply)
    except HTTPException as e:
        yield _error_event(session_id, str(e.detail), e.status_code)
        return

    try:
        plan = _PLAN_ADAPTER.validate_python(parsed)
    except ValidationError as e:
        yield _error_event(session_id, f"Agent JSON shape invalid: {e}", 502)
        return

    if isinstance(plan, ClarifyPlan):
        logger.info("chat clarify in %.1fs: %s", time.monotonic() - t_start, plan.question[:100])
        yield _event(
            {
                "event": "clarify",
                "session_id": session_id,
                "question": plan.question,
                "options": plan.options,
            }
        )
        return

    assert isinstance(plan, PlotPlan)
    logger.info(
        "chat plot in %.1fs: %d signals × %d traces",
        time.monotonic() - t_start,
        len(plan.signals),
        len(plan.traces),
    )
    yield _event(
        {
            "event": "plot",
            "session_id": session_id,
            "plan": plan.model_dump(),
        }
    )


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the LAST ```json … ``` block out of the agent's reply.

    The agent is allowed to "think out loud" before committing — we always
    take the final fence so accidental earlier code blocks don't poison
    the parse.
    """
    matches = JSON_FENCE_RE.findall(text)
    if not matches:
        raise HTTPException(
            status_code=502,
            detail="Agent response did not contain a ```json``` block.",
        )
    try:
        parsed = json.loads(matches[-1])
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502, detail=f"Agent JSON did not parse: {e}"
        ) from e
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail="Agent JSON was not an object.")
    return parsed
```

- [ ] **Step 5: Wire chat router into `main.py`**

Modify `telemetry-comparison/main.py` (find the existing router includes around line 50):

Existing:
```python
import track_loader
import video_proxy
...
app.include_router(track_loader.router)
app.include_router(video_proxy.router)
```

Replace with:
```python
import chat
import track_loader
import video_proxy
...
app.include_router(track_loader.router)
app.include_router(video_proxy.router)
app.include_router(chat.router)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd telemetry-comparison && uv run pytest tests/test_chat.py -v`
Expected: 9 passed.

- [ ] **Step 7: Run full backend gates**

Run: `cd telemetry-comparison && uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest`
Expected: all pass; full backend test suite green.

- [ ] **Step 8: Commit**

```bash
git add telemetry-comparison/chat.py telemetry-comparison/tests/test_chat.py telemetry-comparison/main.py telemetry-comparison/pyproject.toml telemetry-comparison/uv.lock
git commit -m "Add /api/chat streaming route forwarding Quix AI agent"
```

---

## Task 5: Self-host markdown-it + render module

**Files:**
- Create: `telemetry-comparison/static/vendor/markdown-it.js`
- Create: `telemetry-comparison/static/modules/markdown.js`
- Modify: `telemetry-comparison/static/index.html`

The standalone `telemetry-chat/` already self-hosts markdown-it. Copy that bundle byte-for-byte; we don't want a CDN dep.

- [ ] **Step 1: Copy the self-hosted bundle**

Run from repo root:

```bash
git show origin/feature/sc-72213/understand-quixai-marriage-with-api:telemetry-chat/static/vendor/markdown-it.js \
  > telemetry-comparison/static/vendor/markdown-it.js
```

Verify file size > 100 KB and starts with `/*!` or similar comment header:

```bash
ls -lh telemetry-comparison/static/vendor/markdown-it.js
head -1 telemetry-comparison/static/vendor/markdown-it.js
```

- [ ] **Step 2: Add the render module**

Create `telemetry-comparison/static/modules/markdown.js`:

```javascript
/**
 * markdown-it wrapper with a custom link renderer:
 *   - target=_blank + noopener for every link
 *   - reject schemes other than http(s) (mailto/javascript blocked)
 *
 * Self-hosted via the importmap in index.html so we don't pin a CDN URL.
 */

import MarkdownIt from 'markdown-it';

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const href = tokens[idx].attrGet('href') || '';
  if (!/^https?:\/\//i.test(href)) tokens[idx].attrSet('href', '#');
  tokens[idx].attrSet('target', '_blank');
  tokens[idx].attrSet('rel', 'noopener noreferrer');
  return defaultLinkOpen(tokens, idx, options, env, self);
};

export function renderMarkdown(text) {
  return md.render(text || '');
}
```

- [ ] **Step 3: Add the importmap to `index.html`**

Modify `telemetry-comparison/static/index.html` — insert immediately before `<link rel="stylesheet" href="/static/styles.css" />` (around line 38):

```html
<!-- Self-hosted markdown-it for chat answer rendering. Importmap lets
     ES modules `import MarkdownIt from "markdown-it"` resolve to the
     local vendor bundle without a CDN dep. -->
<script type="importmap">
  {
    "imports": {
      "markdown-it": "/static/vendor/markdown-it.js"
    }
  }
</script>
```

- [ ] **Step 4: Smoke-load**

Run dev server (already running from earlier; verify):

```bash
curl -sI http://127.0.0.1:8765/static/vendor/markdown-it.js | head -1
curl -sI http://127.0.0.1:8765/static/modules/markdown.js | head -1
```

Expected: both `HTTP/1.1 200 OK`.

In a browser console at `http://127.0.0.1:8765`, run:

```javascript
const m = await import('/static/modules/markdown.js');
m.renderMarkdown('**hi** [link](https://example.com)');
```

Expected: returns `<p><strong>hi</strong> <a href="https://example.com" target="_blank" rel="noopener noreferrer">link</a></p>` (target attrs proves custom rule fired).

- [ ] **Step 5: Commit**

```bash
git add telemetry-comparison/static/vendor/markdown-it.js telemetry-comparison/static/modules/markdown.js telemetry-comparison/static/index.html
git commit -m "Self-host markdown-it + add chat markdown render helper"
```

---

## Task 6: Rewrite frontend `chat.js` to call real backend

**Files:**
- Modify: `telemetry-comparison/static/modules/chat.js` (full rewrite)

- [ ] **Step 1: Rewrite `chat.js`**

Replace the entire content of `telemetry-comparison/static/modules/chat.js` with:

```javascript
/**
 * Chat UI glue. Submits prompts to /api/chat (JSONL stream), renders
 * assistant replies (status / answer_delta / answer_break / clarify /
 * plot / error), and forwards plot plans to applyPlotPlan so they drive
 * the existing manual UI surfaces (dropdowns + lap chips + signal chips
 * + Plot button).
 *
 * Wire flow:
 *   user types -> submit() -> POST /api/chat
 *   response body = ndjson, read line by line
 *   each line dispatched to handleEvent()
 *   plot event -> applyPlotPlan(plan) -> existing /api/telemetry pipeline
 */

import { applyPlotPlan } from './ai-plot-glue.js';
import { renderMarkdown } from './markdown.js';

let _sessionId = null;
let _activeAnswer = null; // current accumulating assistant bubble
let _sending = false;

const _pendingRender = new Set();
let _renderScheduled = false;

function _scrollBottom(el) {
  el.scrollTop = el.scrollHeight;
}

function _scheduleRender(body) {
  _pendingRender.add(body);
  if (_renderScheduled) return;
  _renderScheduled = true;
  requestAnimationFrame(() => {
    _renderScheduled = false;
    for (const el of _pendingRender) {
      el.innerHTML = renderMarkdown(el.dataset.raw || '');
    }
    _pendingRender.clear();
    const list = document.getElementById('chat-messages');
    if (list) _scrollBottom(list);
  });
}

function _addMessage(role, text) {
  const list = document.getElementById('chat-messages');
  if (!list) return null;
  const div = document.createElement('div');
  div.className = `chat-msg chat-msg-${role}`;
  if (role === 'assistant') {
    div.dataset.raw = text;
    div.innerHTML = renderMarkdown(text);
  } else {
    div.textContent = text;
  }
  list.appendChild(div);
  _scrollBottom(list);
  return div;
}

function _showProgress(label) {
  const list = document.getElementById('chat-messages');
  if (!list) return;
  let prog = document.getElementById('chat-progress');
  if (!prog) {
    prog = document.createElement('div');
    prog.id = 'chat-progress';
    prog.className = 'chat-msg chat-msg-assistant-status';
    list.appendChild(prog);
  }
  prog.textContent = label;
  _scrollBottom(list);
}

function _hideProgress() {
  document.getElementById('chat-progress')?.remove();
}

function _addClarifyChips(options, messageEl) {
  if (!options?.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'chat-clarify-options';
  for (const opt of options) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chat-clarify-chip';
    b.textContent = opt;
    b.addEventListener('click', () => {
      const input = document.getElementById('chat-input');
      if (!input) return;
      input.value = opt;
      _submit();
    });
    wrap.appendChild(b);
  }
  messageEl.appendChild(wrap);
}

async function _readEventStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed) _parseAndHandle(trimmed);
    }
  }
  const tail = buffer.trim();
  if (tail) _parseAndHandle(tail);
}

function _parseAndHandle(line) {
  try {
    _handleEvent(JSON.parse(line));
  } catch (e) {
    console.error('chat: malformed event', e, line.slice(0, 200));
  }
}

function _handleEvent(evt) {
  if (evt.session_id) _sessionId = evt.session_id;
  switch (evt.event) {
    case 'status':
      _activeAnswer = null;
      _showProgress(evt.message);
      break;
    case 'answer_delta': {
      _hideProgress();
      if (!_activeAnswer) {
        _activeAnswer = _addMessage('assistant', '');
      }
      _activeAnswer.dataset.raw = (_activeAnswer.dataset.raw || '') + evt.text;
      _scheduleRender(_activeAnswer);
      break;
    }
    case 'answer_break':
      _activeAnswer = null;
      break;
    case 'clarify': {
      _hideProgress();
      _activeAnswer = null;
      const msg = _addMessage('assistant', evt.question);
      if (msg) _addClarifyChips(evt.options || [], msg);
      break;
    }
    case 'plot':
      _hideProgress();
      _activeAnswer = null;
      applyPlotPlan(evt.plan);
      break;
    case 'error':
      _hideProgress();
      _activeAnswer = null;
      _addMessage(
        'error',
        `${evt.detail}${evt.status ? ` (${evt.status})` : ''}`.slice(0, 500),
      );
      break;
  }
}

async function _submit() {
  const input = document.getElementById('chat-input');
  if (!input || _sending) return;
  const text = input.value.trim();
  if (!text) return;
  _sending = true;
  input.value = '';

  _activeAnswer = null;
  _addMessage('user', text);
  _showProgress('Thinking…');

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: _sessionId }),
    });
    if (!res.ok || !res.body) {
      _hideProgress();
      const detail = await res.text();
      _addMessage('error', `Backend error (${res.status}): ${detail.slice(0, 400)}`);
      return;
    }
    await _readEventStream(res.body);
  } catch (err) {
    _hideProgress();
    _addMessage('error', `Network error: ${err.message}`);
  } finally {
    _sending = false;
    input.focus();
  }
}

export function initChat() {
  const sendBtn = document.getElementById('chat-send');
  const input = document.getElementById('chat-input');
  if (!sendBtn || !input) return;

  sendBtn.addEventListener('click', _submit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      _submit();
    }
  });
}
```

- [ ] **Step 2: Verify dev server hot-reloads + module loads**

Run: `curl -sf http://127.0.0.1:8765/static/modules/chat.js | head -3`
Expected: prints docstring beginning with `/** Chat UI glue. ...`.

- [ ] **Step 3: Run prettier**

Run: `cd telemetry-comparison && node_modules/.bin/prettier --write static/modules/chat.js`
Expected: no errors. File reformatted in place if needed.

- [ ] **Step 4: Commit**

```bash
git add telemetry-comparison/static/modules/chat.js
git commit -m "Replace mock chat with real /api/chat JSONL reader"
```

---

## Task 7: Add chat polish CSS (status, clarify chips, error bubble)

**Files:**
- Modify: `telemetry-comparison/static/styles.css`

The current `chat-msg-assistant-status` class exists from slice 1; the rest are new. Append at the end of the file (after the existing chat panel block).

- [ ] **Step 1: Append new chat polish rules**

Append to `telemetry-comparison/static/styles.css`:

```css
/* =============================================================================
 * Chat backend polish — status spinner, clarify chips, error bubble,
 * markdown-rendered assistant body. Scoped to .chat-msg-* classes that
 * chat.js writes; styles.css already covers the panel chrome.
 * ============================================================================= */

#chat-progress {
  background: transparent;
  color: var(--color-muted, #94a3b8);
  font-style: italic;
  align-self: flex-start;
  display: flex;
  align-items: center;
  gap: 8px;
}

#chat-progress::before {
  content: '';
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--color-muted, #94a3b8);
  border-top-color: var(--color-accent, #63b3ed);
  border-radius: 50%;
  animation: chat-spin 0.8s linear infinite;
}

@keyframes chat-spin {
  to {
    transform: rotate(360deg);
  }
}

/* Markdown-rendered assistant body — tighten default browser styles. */
.chat-msg-assistant > p {
  margin: 0 0 6px 0;
}
.chat-msg-assistant > p:last-child {
  margin-bottom: 0;
}
.chat-msg-assistant > ul,
.chat-msg-assistant > ol {
  margin: 0 0 6px 18px;
  padding: 0;
}
.chat-msg-assistant code {
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 0.85em;
}
.chat-msg-assistant pre {
  background: rgba(0, 0, 0, 0.35);
  padding: 8px 10px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 0.82em;
}
.chat-msg-assistant table {
  border-collapse: collapse;
  margin: 6px 0;
}
.chat-msg-assistant th,
.chat-msg-assistant td {
  border: 1px solid var(--color-brd, #2d3441);
  padding: 3px 8px;
  font-size: 0.85em;
}
.chat-msg-assistant a {
  color: var(--color-accent, #63b3ed);
}

/* Clarify chips — appear inline beneath an assistant question bubble. */
.chat-clarify-options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.chat-clarify-chip {
  border: 1px solid var(--color-brd, #2d3441);
  background: transparent;
  color: var(--color-muted, #94a3b8);
  cursor: pointer;
  padding: 4px 10px;
  font: inherit;
  font-size: 0.78rem;
  border-radius: 999px;
  transition:
    border-color 0.15s,
    color 0.15s;
}
.chat-clarify-chip:hover {
  border-color: var(--color-accent, #63b3ed);
  color: var(--color-accent, #63b3ed);
}

/* Error bubble — red, pinned-left, no markdown rendering. */
.chat-msg-error {
  background: rgba(248, 113, 113, 0.12);
  border: 1px solid rgba(248, 113, 113, 0.4);
  color: #fecaca;
  align-self: flex-start;
  padding: 8px 10px;
  border-radius: 8px;
  max-width: 85%;
  word-break: break-word;
  font-size: 0.85rem;
}
```

- [ ] **Step 2: Format check**

Run: `cd telemetry-comparison && node_modules/.bin/prettier --check static/styles.css`
If it reports unformatted, run `node_modules/.bin/prettier --write static/styles.css`.

- [ ] **Step 3: Verify dev server reloaded**

Run: `curl -sf http://127.0.0.1:8765/static/styles.css | grep -c "chat-clarify-chip"`
Expected: `2` or more (rule + hover).

- [ ] **Step 4: Commit**

```bash
git add telemetry-comparison/static/styles.css
git commit -m "Add chat polish CSS — status spinner, clarify chips, error bubble"
```

---

## Task 8: Add frontend chat tests (vitest)

**Files:**
- Create: `telemetry-comparison/static/modules/chat.test.js`

If `vitest` isn't already a dev dep on telemetry-comparison's `package.json`, this task adds it. Check first.

- [ ] **Step 1: Verify vitest availability**

Run: `cd telemetry-comparison && node_modules/.bin/vitest --version 2>&1 | head -1`

If NOT installed, install:

```bash
cd telemetry-comparison && npm install --save-dev vitest jsdom
```

And add to `package.json` `scripts`:

```json
"test": "vitest run --environment jsdom"
```

- [ ] **Step 2: Write the failing tests**

Create `telemetry-comparison/static/modules/chat.test.js`:

```javascript
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

beforeEach(() => {
  document.body.innerHTML = `
    <div id="chat-messages"></div>
    <textarea id="chat-input"></textarea>
    <button id="chat-send"></button>
  `;
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

function _ndjson(events) {
  const body = events.map((e) => JSON.stringify(e)).join('\n');
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
}

function _stubFetch(events) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      body: _ndjson(events),
      text: async () => '',
    })),
  );
}

async function _flush() {
  // Allow stream reader + rAF batches to settle.
  await new Promise((r) => setTimeout(r, 10));
  await new Promise((r) => requestAnimationFrame(r));
}

describe('chat.js JSONL handling', () => {
  it('renders answer_delta chunks as a single assistant bubble', async () => {
    _stubFetch([
      { event: 'status', session_id: 's1', message: 'Thinking…' },
      { event: 'answer_delta', session_id: 's1', text: 'Hello ' },
      { event: 'answer_delta', session_id: 's1', text: 'world.' },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    document.getElementById('chat-input').value = 'hi';
    document.getElementById('chat-send').click();
    await _flush();

    const bubbles = document.querySelectorAll('.chat-msg-assistant');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].dataset.raw).toBe('Hello world.');
  });

  it('answer_break splits prose into two bubbles', async () => {
    _stubFetch([
      { event: 'answer_delta', session_id: 's', text: 'Pre.' },
      { event: 'answer_break', session_id: 's' },
      { event: 'answer_delta', session_id: 's', text: 'Post.' },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    document.getElementById('chat-input').value = 'q';
    document.getElementById('chat-send').click();
    await _flush();

    const bubbles = document.querySelectorAll('.chat-msg-assistant');
    expect(bubbles).toHaveLength(2);
  });

  it('plot event calls applyPlotPlan', async () => {
    const applySpy = vi.fn(() => true);
    vi.doMock('./ai-plot-glue.js', () => ({ applyPlotPlan: applySpy }));
    _stubFetch([
      {
        event: 'plot',
        session_id: 's',
        plan: { type: 'plot', signals: ['speedKmh'], traces: [] },
      },
    ]);

    const { initChat } = await import('./chat.js');
    initChat();
    document.getElementById('chat-input').value = 'plot';
    document.getElementById('chat-send').click();
    await _flush();

    expect(applySpy).toHaveBeenCalledWith({
      type: 'plot',
      signals: ['speedKmh'],
      traces: [],
    });
  });

  it('clarify event renders option chips', async () => {
    _stubFetch([
      {
        event: 'clarify',
        session_id: 's',
        question: 'Which?',
        options: ['a', 'b'],
      },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    document.getElementById('chat-input').value = 'show';
    document.getElementById('chat-send').click();
    await _flush();

    const chips = document.querySelectorAll('.chat-clarify-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent).toBe('a');
    expect(chips[1].textContent).toBe('b');
  });

  it('error event renders red bubble', async () => {
    _stubFetch([
      { event: 'error', session_id: 's', detail: 'boom', status: 502 },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    document.getElementById('chat-input').value = 'x';
    document.getElementById('chat-send').click();
    await _flush();

    const err = document.querySelector('.chat-msg-error');
    expect(err).not.toBeNull();
    expect(err.textContent).toContain('boom');
    expect(err.textContent).toContain('502');
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd telemetry-comparison && npm test`
Expected: 5 passed.

If tests fail because `./markdown.js` import resolution fails inside vitest+jsdom (importmap is browser-only), add a vitest stub:

Create `telemetry-comparison/vitest.setup.js`:
```javascript
import { vi } from 'vitest';
vi.mock('./markdown.js', () => ({ renderMarkdown: (t) => t || '' }));
```

And in `package.json`:
```json
"test": "vitest run --environment jsdom --setupFiles ./vitest.setup.js"
```

Re-run `npm test`. Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git add telemetry-comparison/static/modules/chat.test.js telemetry-comparison/package.json telemetry-comparison/package-lock.json telemetry-comparison/vitest.setup.js
git commit -m "Add vitest cases for chat JSONL event dispatch"
```

---

## Task 9: Manual smoke + README

**Files:**
- Modify: `telemetry-comparison/README.md`

- [ ] **Step 1: Set local Quix env vars**

Add to `telemetry-comparison/.env` (do NOT commit):

```
QUIX_TOKEN=<your-PAT-or-SDK-token-with-access-to-the-querier-agent>
Quix__Portal__Api=https://portal-api.dev.quix.io
QUIX_AI_AGENT_ID=d578e2f5-c2b7-461a-90d2-70dfac450fb0
```

- [ ] **Step 2: Restart dev server**

In the terminal running the FastAPI dev server: stop it (Ctrl-C) then re-run:

```bash
cd telemetry-comparison && uv run fastapi dev main.py --port 8765
```

The watcher only picks up new env vars on full restart.

- [ ] **Step 3: Smoke test all four modes**

Open `http://127.0.0.1:8765`, hard-refresh (Cmd+Shift+R), run `localStorage.removeItem('telemetryExplorer.chatPanel.v1')` to retrigger first-visit auto-open. Then in the chat panel, type each prompt and verify the matching outcome:

| Prompt | Expected event(s) | Visible result |
|---|---|---|
| `plot Ludvik laps 2-3 ks_nurburgring speed throttle` | `status` -> (optional `answer_delta`) -> `plot` | Charts render, dropdowns auto-fill with Ludvik / ks_nurburgring / bmw_1m. |
| `plot ludvik bmw` (intentionally vague) | `status` -> `clarify` | Two-or-more option chips appear under an assistant question; clicking a chip resends as next message. |
| `fastest lap on bmw_1m at ks_nurburgring?` | `status` -> `answer_delta` chunks (-> optional `tool_call_start` -> `answer_break` -> more `answer_delta`) | Markdown-rendered prose answer with the lap time. |
| `find anomalies in tyre temps` | `status` -> `answer_delta` (single short refusal) | One assistant bubble saying analysis is not supported yet. |

Capture each outcome (screenshot or copy-paste of chat) for the PR description.

- [ ] **Step 4: Update README**

Modify `telemetry-comparison/README.md` to document the chat env vars. Find the existing "Environment" or "Configuration" section and append (or create a new section if absent):

```markdown
## AI chat

The chat panel calls Quix AI's QuixLake Querier agent via `POST /api/chat`. Required env vars:

| Var | Default | Notes |
|---|---|---|
| `Quix__Portal__Api` | (required) | Quix Portal API base. Auto-injected in Quix Cloud. |
| `QUIX_TOKEN` | (required) | Bearer token for `/ai/api/...`. SDK token with org access. |
| `QUIX_AI_AGENT_ID` | `d578e2f5-c2b7-461a-90d2-70dfac450fb0` | QuixLake Querier agent UUID. |

The agent's system prompt + knowledge bases live on the agent itself; this service only forwards user messages and streams responses.
```

- [ ] **Step 5: Commit**

```bash
git add telemetry-comparison/README.md
git commit -m "Document /api/chat env vars in README"
```

---

## Task 10: Open PR

**Files:** none

- [ ] **Step 1: Verify branch state**

Run: `git log origin/main..HEAD --oneline`
Expected: ~10 commits (slice 1 + slice 2 + spec + 7 implementation commits from Tasks 1–9).

- [ ] **Step 2: Run full quality gates one last time**

Backend:
```bash
cd telemetry-comparison && uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
```

Frontend:
```bash
cd telemetry-comparison && npm run format:check && npm test
```

Expected: every gate green.

- [ ] **Step 3: Push branch + open PR**

```bash
git push -u origin feature/sc-72383/integrate-quix-ai-chat-into-telemetry
```

Then open the PR. Title under 70 chars; body is 5 tight bullets per repo style.

Suggested title: `Integrate Quix AI chat into Telemetry Explorer`

Suggested body (the 5 bullets cover the whole branch — slice 1 + slice 2 + backend):

```markdown
- Add floating + dockable AI chat panel inside Telemetry Explorer (drag/resize, viewport-aware auto-switch ≥1440×800)
- Wire `POST /api/chat` to the Quix AI QuixLake Querier agent (`d578e2f5-…`); JSONL streaming with status/answer_delta/answer_break/clarify/plot/error events; no backend lake fan-out (frontend's existing applyPlotPlan drives charts)
- Three modes supported: Mode 1 (plot|clarify) drives the manual UI surfaces; Mode 2 prose answer with markdown + tool-break bubble; Mode 3 defer for ML/clustering/FFT requests
- Self-host markdown-it via importmap + render module; clarify chips, status spinner, error bubble styled to match Explorer chrome
- Backend pytest (test_plans + test_chat with respx mocks) and frontend vitest (chat.test.js) cover happy paths + error mapping; design + plan committed under docs/superpowers/
```

---

## Out of scope (intentionally deferred)

- Test-Manager-aware context (option C from brainstorming) — would need iframe `postMessage` from TM carrying `test_id` + requirements + logbook entries. Picked up after the AI analysis pipeline thread reactivates.
- Conversation history across reloads — would need a backend session store + `/api/chat/history` endpoint. Not requested.
- Decommissioning the standalone `telemetry-chat/` deployment — stays as fallback / playground, no further development.
- Per-user auth on `/api/chat` — same anonymous posture as `/api/telemetry`. Revisit when Explorer overall gets per-user auth.
- ESLint parser config rejecting ES module syntax — pre-existing global config bug; out of scope (would touch unrelated files).
