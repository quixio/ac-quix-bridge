"""POST /api/chat — JSONL streaming integration."""

from __future__ import annotations

import json
from typing import Any

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
    respx.post("https://portal.test/ai/api/sessions").respond(200, json={"id": "sess-1"})
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
    respx.post("https://portal.test/ai/api/sessions/sess-1/messages").respond(200, content=sse_body)

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
    respx.post("https://portal.test/ai/api/sessions").respond(200, json={"id": "sess-2"})
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
    respx.post("https://portal.test/ai/api/sessions/sess-2/messages").respond(200, content=sse_body)

    with TestClient(app) as client:
        events = _read_jsonl(client.post("/api/chat", json={"message": "plot ludvik"}).content)

    clarify = next(e for e in events if e["event"] == "clarify")
    assert clarify["question"] == "Which session?"
    assert clarify["options"] == ["a", "b"]


@respx.mock
def test_analysis_mode_streams_answer_delta_only() -> None:
    """Mode 2 / Mode 3 — no JSON fence, just prose. Stream answer_delta, no plot."""
    respx.post("https://portal.test/ai/api/sessions").respond(200, json={"id": "sess-3"})
    sse_body = _sse(
        [
            {"type": "text_delta", "text": "Tomas's lap "},
            {"type": "text_delta", "text": "3 was fastest."},
        ]
    )
    respx.post("https://portal.test/ai/api/sessions/sess-3/messages").respond(200, content=sse_body)

    with TestClient(app) as client:
        events = _read_jsonl(client.post("/api/chat", json={"message": "fastest lap?"}).content)

    kinds = [e["event"] for e in events]
    assert "plot" not in kinds
    assert "clarify" not in kinds
    deltas = [e for e in events if e["event"] == "answer_delta"]
    assert "".join(d["text"] for d in deltas) == "Tomas's lap 3 was fastest."


@respx.mock
def test_tool_call_emits_answer_break() -> None:
    """Mode 2 with tool use — answer_break splits pre/post-tool prose."""
    respx.post("https://portal.test/ai/api/sessions").respond(200, json={"id": "sess-4"})
    sse_body = _sse(
        [
            {"type": "text_delta", "text": "Querying lake. "},
            {"type": "tool_call_start", "toolName": "mcp__abc__run_query"},
            {"type": "tool_result", "result": "csv,here"},
            {"type": "text_delta", "text": "Tomas was fastest."},
        ]
    )
    respx.post("https://portal.test/ai/api/sessions/sess-4/messages").respond(200, content=sse_body)

    with TestClient(app) as client:
        events = _read_jsonl(client.post("/api/chat", json={"message": "fastest lap?"}).content)

    kinds = [e["event"] for e in events]
    assert "answer_break" in kinds


@respx.mock
def test_agent_5xx_emits_error_event() -> None:
    respx.post("https://portal.test/ai/api/sessions").respond(200, json={"id": "sess-5"})
    respx.post("https://portal.test/ai/api/sessions/sess-5/messages").respond(
        503, text="upstream unavailable"
    )

    with TestClient(app) as client:
        events = _read_jsonl(client.post("/api/chat", json={"message": "x"}).content)

    err = next(e for e in events if e["event"] == "error")
    assert err["status"] == 502  # we re-classify Quix Portal 503 as our 502


@respx.mock
def test_session_id_reused_when_provided() -> None:
    """Frontend sends session_id on follow-up turns; backend skips create."""
    create_route = respx.post("https://portal.test/ai/api/sessions").respond(
        200, json={"id": "should-not-be-called"}
    )
    sse_body = _sse([{"type": "text_delta", "text": "ok"}])
    respx.post("https://portal.test/ai/api/sessions/existing-sess/messages").respond(
        200, content=sse_body
    )

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
        r = client.post("/api/chat", json={"message": "x", "session_id": "bad id with spaces"})
        assert r.status_code == 422
