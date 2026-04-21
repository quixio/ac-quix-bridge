"""TypedDict shapes for Quix Portal AI responses we proxy.

`total=False` throughout: we only model fields our code touches or that callers
rely on; Portal may add more and we pass them through verbatim.
"""

from __future__ import annotations

from typing import TypedDict


class SessionSummary(TypedDict, total=False):
    id: str
    status: str
    title: str | None
    createdAt: str
    lastActivityAt: str
    messageCount: int


class Message(TypedDict, total=False):
    id: str
    role: str
    content: str | None
    contentBlocks: list[dict[str, object]] | None
    sequenceNumber: int
    synthetic: bool
    createdAt: str


class SessionDetail(SessionSummary, total=False):
    messages: list[Message]
    hasMoreMessages: bool


class MessagesPage(TypedDict, total=False):
    messages: list[Message]
    hasMore: bool
