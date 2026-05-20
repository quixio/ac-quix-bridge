"""Bearer-token auth gate."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import auth
import config
from main import app

# Opt out of the conftest auth bypass — these tests exercise the real
# middleware end-to-end.
pytestmark = pytest.mark.real_auth


VALID_TOKEN = "valid-token-123456"
WORKSPACE = "workspace-abc"


class _StubAuth:
    """Mimics `quixportal.auth.Auth` — accepts only `VALID_TOKEN`."""

    def validate_permissions(
        self, token: str, resource_type: str, resource_id: str, permission: str
    ) -> bool:
        return token == VALID_TOKEN and resource_id == WORKSPACE


@pytest.fixture(autouse=True)
def _stub_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "API_AUTH_ACTIVE", True)
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", False)
    monkeypatch.setattr(config, "WORKSPACE_ID", WORKSPACE)
    monkeypatch.setattr(auth, "_auth_impl", lambda: _StubAuth())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_api_route_requires_bearer(client: TestClient) -> None:
    r = client.get("/api/channels")
    assert r.status_code == 401
    assert r.json()["detail"] == "Not Authenticated"
    assert "Bearer" in r.headers.get("WWW-Authenticate", "")


def test_api_route_rejects_invalid_token(client: TestClient) -> None:
    r = client.get("/api/channels", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_api_route_accepts_valid_bearer(client: TestClient) -> None:
    r = client.get("/api/channels", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert r.status_code == 200


def test_lowercase_bearer_accepted(client: TestClient) -> None:
    r = client.get("/api/channels", headers={"Authorization": f"bearer {VALID_TOKEN}"})
    assert r.status_code == 200


def test_non_bearer_scheme_rejected(client: TestClient) -> None:
    """Only `Bearer ` (or `bearer `) is accepted — Basic/Digest/raw are rejected."""
    r = client.get("/api/channels", headers={"Authorization": f"Basic {VALID_TOKEN}"})
    assert r.status_code == 401


def test_raw_token_without_scheme_rejected(client: TestClient) -> None:
    r = client.get("/api/channels", headers={"Authorization": VALID_TOKEN})
    assert r.status_code == 401


def test_empty_authorization_header_rejected(client: TestClient) -> None:
    r = client.get("/api/channels", headers={"Authorization": ""})
    assert r.status_code == 401


def test_public_paths_open_without_token(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200


def test_static_files_open_without_token(client: TestClient) -> None:
    """SPA shell + assets load before the token handshake completes."""
    r = client.get("/static/index.html")
    # The file may not exist in the test layout, but routing must reach the
    # static mount (i.e. NOT be 401-blocked by middleware).
    assert r.status_code != 401


def test_api_auth_active_false_bypasses_everything(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "API_AUTH_ACTIVE", False)
    r = client.get("/api/channels")  # no token at all
    assert r.status_code == 200


def test_local_dev_mode_uses_local_auth(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LOCAL_DEV_MODE swaps in LocalAuth, which grants everything."""
    from local_auth import LocalAuth

    monkeypatch.setattr(auth, "_auth_impl", lambda: LocalAuth())
    monkeypatch.setattr(config, "LOCAL_DEV_MODE", True)
    r = client.get("/api/channels", headers={"Authorization": "Bearer literally-anything"})
    assert r.status_code == 200


def test_validation_exception_returns_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Portal SDK / network failure — not a bad token. Surface as 503 so
    callers can retry instead of dropping the session as auth-failed."""

    class _BoomAuth:
        def validate_permissions(self, *_a: object, **_kw: object) -> bool:
            raise RuntimeError("portal exploded")

    monkeypatch.setattr(auth, "_auth_impl", lambda: _BoomAuth())
    r = client.get("/api/channels", headers={"Authorization": f"Bearer {VALID_TOKEN}"})
    assert r.status_code == 503
    assert r.json()["detail"] == "Auth service unavailable"
