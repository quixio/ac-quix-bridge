"""Test fixtures for telemetry-comparison.

We do NOT hit a real Lakehouse. Tests use `stub_lake` to swap `main._lake_http`
with an httpx.AsyncClient backed by a MockTransport that returns a canned
CSV body for every POST to /api/query. That keeps tests fast and deterministic.

`config_env` ensures `config.LAKEHOUSE_QUERY_URL` / `LAKEHOUSE_QUERY_TOKEN` look set so the
env-var guard inside `_lake_query` doesn't short-circuit before the mock fires.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import auth
import config
import main


@pytest.fixture(autouse=True)
def _bypass_auth(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the Bearer-token gate for every test except the ones in
    test_auth.py that exercise the gate directly. Tests that need the real
    middleware can request the `real_auth` marker."""
    if request.node.get_closest_marker("real_auth"):
        return
    monkeypatch.setattr(config, "API_AUTH_ACTIVE", False)
    monkeypatch.setattr(auth, "_auth_impl", lambda: _AlwaysAllow())


class _AlwaysAllow:
    def validate_permissions(self, *_args: object, **_kwargs: object) -> bool:
        return True


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend LAKEHOUSE_QUERY_URL + LAKEHOUSE_QUERY_TOKEN are configured. The values
    don't matter — the mock transport intercepts the outbound request."""
    monkeypatch.setattr(config, "LAKEHOUSE_QUERY_URL", "https://test-lake.example.com")
    monkeypatch.setattr(config, "LAKEHOUSE_QUERY_TOKEN", "test-token")
    monkeypatch.setattr(config, "TABLE_NAME", "ac_telemetry")


class _LakeStub:
    """Records SQL submitted to the mocked lake and serves a queued CSV body
    (or a 4xx/5xx response for error-path tests)."""

    def __init__(self) -> None:
        self.last_sql: str | None = None
        self._csv: str = "normalizedCarPosition\n"  # empty-ish default
        self._status: int = 200

    def set_csv(self, csv: str) -> None:
        self._csv = csv
        self._status = 200

    def set_response(self, status: int, body: str = "") -> None:
        self._status = status
        self._csv = body


@pytest.fixture
def stub_lake(monkeypatch: pytest.MonkeyPatch, config_env: None) -> _LakeStub:
    """Patch main._lake_http so any POST to /api/query returns the queued CSV."""
    stub = _LakeStub()

    def transport(request: httpx.Request) -> httpx.Response:
        stub.last_sql = request.content.decode("utf-8") if request.content else ""
        return httpx.Response(stub._status, text=stub._csv)

    monkeypatch.setattr(
        main,
        "_lake_http",
        httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    return stub


@pytest.fixture
def stub_lake_factory(
    monkeypatch: pytest.MonkeyPatch, config_env: None
) -> Callable[[Callable[[str], tuple[int, str]]], Any]:
    """Build a stub whose response depends on the SQL sent (e.g. for tests
    that need different bodies for different `WHERE lap = N` queries).

    Usage:
        def by_lap(sql: str) -> tuple[int, str]:
            return 200, "..." if "lap = 1" in sql else "..."
        stub_lake_factory(by_lap)
    """

    def _build(fn: Callable[[str], tuple[int, str]]) -> None:
        def transport(request: httpx.Request) -> httpx.Response:
            sql = request.content.decode("utf-8") if request.content else ""
            status, body = fn(sql)
            return httpx.Response(status, text=body)

        monkeypatch.setattr(
            main,
            "_lake_http",
            httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        )

    return _build
