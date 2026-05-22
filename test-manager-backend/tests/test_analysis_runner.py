"""Tests for the analysis runner — asyncio task holding Quix.AI SSE."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Generator
from uuid import uuid4

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from pymongo.database import Database
from testcontainers.mongodb import MongoDbContainer

import api.analysis_runner as runner_mod
from api.analysis_runner import cleanup_orphans, run_analysis
from tests.conftest import TestFactory


PORTAL = "https://portal-api.platform.quix.io"


# --- Fixtures ------------------------------------------------------------- #


@pytest.fixture
def mongo_db(
    mongo: None, mongo_container: MongoDbContainer
) -> Generator[Database[dict[str, Any]], None, None]:
    """Direct pymongo Database handle pointing at the testcontainers Mongo."""
    client = mongo_container.get_connection_client()
    yield client[mongo_container.dbname]
    client.close()


@pytest.fixture
def mock_quix_ai(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Force the runner to use the canonical PORTAL URL in tests."""
    monkeypatch.setenv("Quix__Portal__Api", PORTAL)
    monkeypatch.setenv("Quix__Workspace__Id", "ws-test")
    monkeypatch.setenv("QUIX_AI_POST_RACE_AGENT_ID", "agent-test")
    yield


# --- Helpers -------------------------------------------------------------- #


def _create_test_with_session(
    client: TestClient,
    create_test: TestFactory,
    session_id: str = "2026-05-22T10:30:00",
) -> tuple[str, str]:
    """Create a test and attach one session. Returns (test_id, session_id)."""
    _, created = create_test()
    test_id = created["test_id"]
    response = client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={
            "session_id": session_id,
            "track": "ks_nurburgring",
            "car_model": "bmw_1m",
        },
    )
    assert response.status_code == 200
    return test_id, session_id


def _insert_pending(
    mongo: Database[dict[str, Any]], test_id: str, session_id: str
) -> str:
    aid = str(uuid4())
    mongo.analyses.insert_one(
        {
            "_id": aid,
            "schema_version": 1,
            "test_id": test_id,
            "session_id": session_id,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "kpis": [],
            "requirements_check": [],
            "logbook_refs": [],
            "anomalies": [],
            "summary_md": "",
            "extra": {},
        }
    )
    return aid


def _sse(events: list[dict[str, Any]]) -> str:
    """Encode a list of event dicts as SSE wire format."""
    out = []
    for e in events:
        out.append(f"data: {json.dumps(e)}\n\n")
    return "".join(out)


# --- Tests ---------------------------------------------------------------- #


@respx.mock
async def test_runner_happy_path_marks_complete(
    client: TestClient,
    create_test: TestFactory,
    mongo_db: Database[dict[str, Any]],
    mock_quix_ai: None,
) -> None:
    test_id, session_id = _create_test_with_session(client, create_test)
    aid = _insert_pending(mongo_db, test_id, session_id)

    # Mock session-create endpoint
    respx.post(f"{PORTAL}/ai/api/sessions").mock(
        return_value=httpx.Response(200, json={"id": "qsess-1"}),
    )
    # Mock message-stream endpoint with happy-path SSE sequence
    sse_body = _sse(
        [
            {
                "type": "tool_call_start",
                "toolName": "mcp__test-manager__get_test",
                "toolCallId": "tc1",
            },
            {"type": "tool_result", "toolCallId": "tc1", "isError": False},
            {
                "type": "tool_call_start",
                "toolName": "mcp__test-manager__save_analysis",
                "toolCallId": "tc2",
            },
            {"type": "tool_result", "toolCallId": "tc2", "isError": False},
            {
                "type": "usage",
                "inputTokens": 4218,
                "outputTokens": 1132,
                "cacheCreationInputTokens": 100,
                "cacheReadInputTokens": 2000,
                "model": "claude-opus-4-7",
            },
        ]
    )
    respx.post(f"{PORTAL}/ai/api/sessions/qsess-1/messages").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        ),
    )

    # Simulate the MCP save_analysis having already flipped status to complete.
    # In real runs the MCP write path owns the status="complete" transition;
    # the runner only records token + model fields from the usage event.
    mongo_db.analyses.update_one(
        {"_id": aid},
        {"$set": {"status": "complete", "summary_md": "ok"}},
    )

    await run_analysis(
        mongo_db, analysis_id=aid, test_id=test_id, session_id=session_id
    )

    doc = mongo_db.analyses.find_one({"_id": aid})
    assert doc is not None
    assert doc["status"] == "complete"
    assert doc["quix_session_id"] == "qsess-1"
    assert doc["model"] == "claude-opus-4-7"
    assert doc["tokens_in"] == 4218
    assert doc["tokens_out"] == 1132
    assert doc["tokens_cache_create"] == 100
    assert doc["tokens_cache_read"] == 2000
    assert doc["duration_ms"] is not None


@respx.mock
async def test_runner_no_save_marks_failed(
    client: TestClient,
    create_test: TestFactory,
    mongo_db: Database[dict[str, Any]],
    mock_quix_ai: None,
) -> None:
    test_id, session_id = _create_test_with_session(client, create_test)
    aid = _insert_pending(mongo_db, test_id, session_id)

    respx.post(f"{PORTAL}/ai/api/sessions").mock(
        return_value=httpx.Response(200, json={"id": "qsess-2"})
    )

    # SSE ends without save_analysis tool_call_start
    sse_body = _sse(
        [
            {
                "type": "tool_call_start",
                "toolName": "mcp__test-manager__get_test",
                "toolCallId": "tc1",
            },
            {"type": "tool_result", "toolCallId": "tc1", "isError": False},
            {"type": "usage", "inputTokens": 100, "outputTokens": 50},
        ]
    )
    respx.post(f"{PORTAL}/ai/api/sessions/qsess-2/messages").mock(
        return_value=httpx.Response(
            200,
            content=sse_body.encode(),
            headers={"content-type": "text/event-stream"},
        )
    )

    await run_analysis(
        mongo_db, analysis_id=aid, test_id=test_id, session_id=session_id
    )

    doc = mongo_db.analyses.find_one({"_id": aid})
    assert doc is not None
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "agent"


@respx.mock
async def test_runner_sse_drop_marks_failed(
    client: TestClient,
    create_test: TestFactory,
    mongo_db: Database[dict[str, Any]],
    mock_quix_ai: None,
) -> None:
    test_id, session_id = _create_test_with_session(client, create_test)
    aid = _insert_pending(mongo_db, test_id, session_id)

    respx.post(f"{PORTAL}/ai/api/sessions").mock(
        return_value=httpx.Response(200, json={"id": "qsess-3"})
    )
    respx.post(f"{PORTAL}/ai/api/sessions/qsess-3/messages").mock(
        side_effect=httpx.ReadError("connection reset")
    )

    await run_analysis(
        mongo_db, analysis_id=aid, test_id=test_id, session_id=session_id
    )

    doc = mongo_db.analyses.find_one({"_id": aid})
    assert doc is not None
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "agent"


async def test_runner_timeout_marks_failed(
    client: TestClient,
    create_test: TestFactory,
    mongo_db: Database[dict[str, Any]],
    mock_quix_ai: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force a fast timeout by patching HARD_TIMEOUT_SECONDS to 0.1, then have the mock hang."""
    monkeypatch.setattr(runner_mod, "HARD_TIMEOUT_SECONDS", 0.1)

    test_id, session_id = _create_test_with_session(client, create_test)
    aid = _insert_pending(mongo_db, test_id, session_id)

    async def _hang(*_args: Any, **_kwargs: Any) -> httpx.Response:
        await asyncio.sleep(10)
        return httpx.Response(200, json={"id": "qsess-4"})

    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{PORTAL}/ai/api/sessions").mock(side_effect=_hang)
        await run_analysis(
            mongo_db, analysis_id=aid, test_id=test_id, session_id=session_id
        )

    doc = mongo_db.analyses.find_one({"_id": aid})
    assert doc is not None
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "timeout"


def test_cleanup_orphans_marks_stuck_pending_failed(
    client: TestClient,
    create_test: TestFactory,
    mongo_db: Database[dict[str, Any]],
) -> None:
    """Insert a stale running doc with updated_at = 30min ago, run cleanup, verify it's marked."""
    test_id, session_id = _create_test_with_session(client, create_test)
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=30)

    aid = str(uuid4())
    mongo_db.analyses.insert_one(
        {
            "_id": aid,
            "schema_version": 1,
            "test_id": test_id,
            "session_id": session_id,
            "status": "running",
            "created_at": stale_at,
            "updated_at": stale_at,
            "kpis": [],
            "requirements_check": [],
            "logbook_refs": [],
            "anomalies": [],
            "summary_md": "",
            "extra": {},
        }
    )

    n = cleanup_orphans(mongo_db)
    assert n == 1

    doc = mongo_db.analyses.find_one({"_id": aid})
    assert doc is not None
    assert doc["status"] == "failed"
    assert doc["error_kind"] == "orphan"
