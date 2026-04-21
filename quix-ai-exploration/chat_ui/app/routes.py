import json
import re
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from .quix import create_workspace_session, stream_workspace_message

router = APIRouter(prefix="/api")

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not SESSION_ID_RE.fullmatch(v):
            raise ValueError("session_id must be 8-64 chars of [A-Za-z0-9_-]")
        return v


async def _sse_stream(session_id: str, message: str) -> AsyncIterator[bytes]:
    yield f"event: session\ndata: {json.dumps({'session_id': session_id})}\n\n".encode()
    async with httpx.AsyncClient(timeout=120.0) as client:
        async for chunk in stream_workspace_message(client, session_id, message):
            yield chunk


@router.post("/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    session_id = req.session_id
    if not session_id:
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                session_id = await create_workspace_session(client)
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=502, detail="portal unreachable"
                ) from exc
    return StreamingResponse(
        _sse_stream(session_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
