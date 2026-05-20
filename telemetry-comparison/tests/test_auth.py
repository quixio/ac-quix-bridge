"""Shared-password Basic auth gate."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

import config
from main import app

# Opt out of the conftest auth bypass — these tests exercise the real
# middleware end-to-end.
pytestmark = pytest.mark.real_auth


def _basic(user: str, password: str) -> dict[str, str]:
    creds = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


@pytest.fixture(autouse=True)
def _stub_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "SHARED_PASSWORD", "letmein")


def test_no_credentials_returns_401() -> None:
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 401
        assert "Basic" in r.headers.get("WWW-Authenticate", "")


def test_wrong_password_returns_401() -> None:
    with TestClient(app) as c:
        r = c.get("/health", headers=_basic("alice", "nope"))
        assert r.status_code == 401


def test_valid_password_passes() -> None:
    with TestClient(app) as c:
        r = c.get("/health", headers=_basic("alice", "letmein"))
        assert r.status_code == 200


def test_username_is_ignored() -> None:
    """Any username works — only the password is checked."""
    with TestClient(app) as c:
        for user in ("alice", "bob", ""):
            r = c.get("/health", headers=_basic(user, "letmein"))
            assert r.status_code == 200, f"failed for user={user!r}"


def test_static_route_also_gated() -> None:
    """Middleware must cover app.mount(...) too — not just route deps."""
    with TestClient(app) as c:
        r = c.get("/static/index.html")
        assert r.status_code == 401


def test_empty_shared_password_locks_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closed-by-default — empty env var means no caller can pass."""
    monkeypatch.setattr(config, "SHARED_PASSWORD", "")
    with TestClient(app) as c:
        # Even valid-looking creds fail because expected is empty.
        r = c.get("/health", headers=_basic("alice", "anything"))
        assert r.status_code == 401


def test_malformed_basic_header_returns_401() -> None:
    with TestClient(app) as c:
        r = c.get("/health", headers={"Authorization": "Basic not-base64-!!!"})
        assert r.status_code == 401
