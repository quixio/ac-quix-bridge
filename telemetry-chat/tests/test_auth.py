"""HTTP Basic gating: every route 401s without the right password."""

from __future__ import annotations

from base64 import b64encode

import pytest
from fastapi.testclient import TestClient

from app import config, create_app


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(config, "SHARED_PASSWORD", "secret123")
    return TestClient(create_app())


def _basic(user: str, password: str) -> dict[str, str]:
    raw = f"{user}:{password}".encode()
    return {"Authorization": f"Basic {b64encode(raw).decode()}"}


def test_no_credentials_401(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")


def test_wrong_password_401(client: TestClient) -> None:
    r = client.get("/", headers=_basic("anyone", "wrong"))
    assert r.status_code == 401


def test_correct_password_200(client: TestClient) -> None:
    r = client.get("/", headers=_basic("anyone", "secret123"))
    assert r.status_code == 200


def test_health_also_gated(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 401
    r = client.get("/api/health", headers=_basic("u", "secret123"))
    assert r.status_code == 200


def test_static_also_gated(client: TestClient) -> None:
    assert client.get("/static/style.css").status_code == 401


def test_empty_password_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SHARED_PASSWORD", "")
    c = TestClient(create_app())
    assert c.get("/", headers=_basic("u", "")).status_code == 401
    assert c.get("/", headers=_basic("u", "anything")).status_code == 401
