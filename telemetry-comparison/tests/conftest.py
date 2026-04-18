"""Test fixtures for telemetry-comparison.

We do NOT hit a real QuixLake. Instead, tests monkeypatch `get_client()` to
return a stub whose `.query()` returns a canned pandas DataFrame. That keeps
tests fast (<100ms each) and removes the network dependency.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import main


class StubQuixLakeClient:
    """Canned QuixLake client. Whatever `query()` SQL is passed, it returns
    the DataFrame you configured via `set_response(df)`."""

    def __init__(self) -> None:
        self._response: pd.DataFrame = pd.DataFrame()
        self.last_sql: str | None = None

    def set_response(self, df: pd.DataFrame) -> None:
        self._response = df

    def query(self, sql: str) -> pd.DataFrame:  # matches QuixLakeClient.query signature
        self.last_sql = sql
        return self._response.copy()


@pytest.fixture
def stub_client(monkeypatch: pytest.MonkeyPatch) -> StubQuixLakeClient:
    """Replace main.get_client with one that returns a stub whose .query()
    returns canned DataFrames.

    Usage:
        def test_x(stub_client, client):
            stub_client.set_response(pd.DataFrame({"a": [1, 2]}))
            r = client.get("/api/some-endpoint")
            assert r.json() == ...
    """
    stub = StubQuixLakeClient()
    monkeypatch.setattr(main, "get_client", lambda: stub)
    return stub


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture
def stub_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[Callable[[str], pd.DataFrame]], StubQuixLakeClient]:
    """For when different SQL statements need different responses — pass a
    callable that maps SQL → DataFrame and returns the stub for inspection."""

    def _factory(fn: Callable[[str], pd.DataFrame]) -> StubQuixLakeClient:
        stub = StubQuixLakeClient()

        def query(sql: str) -> pd.DataFrame:
            stub.last_sql = sql
            return fn(sql)

        monkeypatch.setattr(stub, "query", query)
        monkeypatch.setattr(main, "get_client", lambda: stub)
        return stub

    return _factory
