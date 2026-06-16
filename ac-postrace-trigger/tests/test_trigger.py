"""Unit tests for the post-race trigger's pure core (POST + key extraction).

These avoid quixstreams entirely (imported lazily in ``main.main()``) so they
run in an ephemeral env: ``uv run --with httpx --with pytest python -m pytest``.
"""

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_trigger_posts_session_id_and_auto_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "AUTH_TOKEN", "svc-token")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(202, json={"analysis_id": "a-1"})

    with _client(handler) as client:
        main.trigger_analysis("2026-06-15T10:00:00.000Z", client=client)

    assert str(seen["url"]).endswith("/api/v1/analyses")
    assert seen["body"] == {
        "session_id": "2026-06-15T10:00:00.000Z",
        "triggered_by": "auto",
    }
    assert seen["auth"] == "svc-token"


def test_trigger_includes_secret_header_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "AUTH_TOKEN", "svc-token")
    monkeypatch.setattr(main, "TRIGGER_SECRET", "s3cr3t")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["secret"] = request.headers.get("x-trigger-secret")
        return httpx.Response(202, json={"analysis_id": "a-1"})

    with _client(handler) as client:
        main.trigger_analysis("S-1", client=client)

    assert seen["secret"] == "s3cr3t"


def test_trigger_omits_secret_header_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "TRIGGER_SECRET", "")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_secret"] = "x-trigger-secret" in request.headers
        return httpx.Response(202, json={"analysis_id": "a-1"})

    with _client(handler) as client:
        main.trigger_analysis("S-1", client=client)

    assert seen["has_secret"] is False


def test_trigger_404_is_benign_skip(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "No test owns session"})

    with caplog.at_level("INFO"), _client(handler) as client:
        # Must not raise — an unlinked session is an expected race.
        main.trigger_analysis("orphan-session", client=client)

    assert any("not linked" in r.message or "404" in r.message for r in caplog.records)


def test_trigger_200_dedup_treated_as_success(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"analysis_id": "existing-1"})

    with caplog.at_level("INFO"), _client(handler) as client:
        main.trigger_analysis("S-1", client=client)

    assert any("existing-1" in r.message for r in caplog.records)


def test_trigger_empty_session_id_skips_without_post() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("must not POST for empty session_id")

    with _client(handler) as client:
        main.trigger_analysis("", client=client)


def test_session_id_from_prefers_value_key() -> None:
    assert main._session_id_from({"key": "S-123", "event": "x"}, b"S-123") == "S-123"


def test_session_id_from_falls_back_to_message_key() -> None:
    assert main._session_id_from({"event": "x"}, b"S-456") == "S-456"
    assert main._session_id_from(None, b"S-789") == "S-789"
