"""Fast unit tests for header/context builders + the SSE error frame."""

from __future__ import annotations

from app.config import portal_context, portal_headers
from app.quix import _error_frame


def test_portal_headers_default_no_accept() -> None:
    h = portal_headers()
    assert h["Content-Type"] == "application/json"
    assert "Accept" not in h
    assert h["Authorization"].startswith("Bearer ")


def test_portal_headers_streaming_sets_accept() -> None:
    h = portal_headers(streaming=True)
    assert h["Accept"] == "text/event-stream"


def test_portal_context_shape() -> None:
    ctx = portal_context()
    assert set(ctx) == {"workspaceId", "workspaceName", "page"}
    assert ctx["page"].startswith("/pipeline?workspace=")


def test_error_frame_status_only() -> None:
    frame = _error_frame(502).decode()
    assert frame.startswith("event: error\n")
    assert '"status": 502' in frame
    assert frame.endswith("\n\n")
    # body must never be forwarded
    assert "body" not in frame
