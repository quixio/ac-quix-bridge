"""Pydantic validator rejects anything that could path-traverse into other Portal routes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.routes import ChatRequest


@pytest.mark.parametrize(
    "session_id",
    [
        "a1b2c3d4",  # min length
        "9a79f9c7-76e8-4641-846f-a55e0ccac6d3",  # real UUID shape
        "abc_DEF-123",  # allowed chars mixed
    ],
)
def test_accepts_valid(session_id: str) -> None:
    req = ChatRequest(message="hi", session_id=session_id)
    assert req.session_id == session_id


def test_accepts_none() -> None:
    assert ChatRequest(message="hi").session_id is None


@pytest.mark.parametrize(
    "session_id",
    [
        "short",  # < 8
        "../../admin",  # path traversal
        "a/b/c",  # slashes
        "abc def",  # space
        "a" * 65,  # > 64
        "abc.def",  # dot
        "",  # empty
    ],
)
def test_rejects_invalid(session_id: str) -> None:
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", session_id=session_id)
