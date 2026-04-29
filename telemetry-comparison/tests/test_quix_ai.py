"""Quix AI client edge cases beyond what test_chat.py covers."""

from __future__ import annotations

import httpx
import pytest
import respx

import config
from quix_ai import create_session


@pytest.fixture(autouse=True)
def _stub_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PORTAL", "https://portal.test")
    monkeypatch.setattr(config, "QUIX_TOKEN", "test-token")
    monkeypatch.setattr(config, "AGENT_CONFIGURATION_ID", "agent-uuid")


@respx.mock
async def test_create_session_raises_on_malformed_response() -> None:
    """Portal returning JSON without 'id' or 'sessionId' must raise HTTPError,
    not KeyError, so the chat route's httpx.HTTPError handler can clean up."""
    respx.post("https://portal.test/ai/api/sessions").respond(200, json={})
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.HTTPError):
            await create_session(client)
