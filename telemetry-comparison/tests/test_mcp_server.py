"""MCP server (/mcp) — plot_data tool behavior + X-API-Key auth.

Tools are tested by calling the handler directly (FastMCP is just transport);
auth is tested over HTTP through the mounted sub-app.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import config
import mcp_server
from main import app

_TRACE: dict[str, Any] = {
    "session_id": "2026-06-03T11:08:18.206Z",
    "lap": 1,
    "driver": "ludvik",
    "carModel": "lambo",
    "track": "spa",
    "experiment": "Exp",
    "environment": "byox",
    "test_rig": "g29",
}

_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}
_SSE_ACCEPT = {"Accept": "application/json, text/event-stream"}


def test_plot_data_returns_confirmation() -> None:
    out = mcp_server.plot_data(signals=["speedKmh", "gas"], traces=[_TRACE], title="Lap 1")
    assert out["status"] == "plotted"
    assert out["trace_count"] == 1
    assert out["signals"] == ["speedKmh", "gas"]


def test_plot_data_rejects_empty_signals() -> None:
    with pytest.raises(ValidationError):
        mcp_server.plot_data(signals=[], traces=[_TRACE])


def test_plot_data_rejects_empty_traces() -> None:
    with pytest.raises(ValidationError):
        mcp_server.plot_data(signals=["speedKmh"], traces=[])


def test_mcp_rejects_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MCP_API_KEY", "secret")
    with TestClient(app) as client:
        r = client.post("/mcp/", json=_INIT_BODY, headers=_SSE_ACCEPT)
    assert r.status_code == 401


def test_mcp_rejects_wrong_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MCP_API_KEY", "secret")
    with TestClient(app) as client:
        r = client.post("/mcp/", json=_INIT_BODY, headers={**_SSE_ACCEPT, "X-API-Key": "nope"})
    assert r.status_code == 401


def test_mcp_accepts_correct_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MCP_API_KEY", "secret")
    with TestClient(app) as client:
        r = client.post("/mcp/", json=_INIT_BODY, headers={**_SSE_ACCEPT, "X-API-Key": "secret"})
    assert r.status_code != 401  # auth passed (MCP transport handles the rest)


def test_mcp_500_when_key_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MCP_API_KEY", "")
    with TestClient(app) as client:
        r = client.post("/mcp/", json=_INIT_BODY, headers={**_SSE_ACCEPT, "X-API-Key": "x"})
    assert r.status_code == 500
