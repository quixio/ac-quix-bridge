"""Fast tests for route-level validation that doesn't hit Portal."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.mark.parametrize(
    "bad_id",
    [
        "short",  # < 8 chars
        "a" * 65,  # > 64 chars
        "abc.def",  # invalid char
        "abc def",  # space
    ],
)
def test_session_detail_rejects_malformed_id(client: TestClient, bad_id: str):
    r = client.get(f"/api/sessions/{bad_id}")
    assert r.status_code == 422


def test_chat_rejects_malformed_session_id(client: TestClient):
    r = client.post("/api/chat", json={"message": "hi", "session_id": "../admin"})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"limit": 0}, 422),  # below min
        ({"limit": 101}, 422),  # above max
        ({"before": -1}, 422),  # negative
    ],
)
def test_messages_rejects_bad_query_params(
    client: TestClient, params: dict[str, int], expected: int
):
    r = client.get(
        "/api/sessions/aaaaaaaabbbbbbbbccccccccdddddddd/messages", params=params
    )
    assert r.status_code == expected


def test_messages_rejects_malformed_session_id(client: TestClient):
    r = client.get("/api/sessions/bad/messages")
    assert r.status_code == 422
