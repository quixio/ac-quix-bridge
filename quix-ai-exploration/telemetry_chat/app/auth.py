"""Shared-password HTTP Basic auth for the whole app.

One `SHARED_PASSWORD` env var, gated on every request via an ASGI
middleware so mounted static files are also covered (FastAPI route-level
dependencies don't reach `app.mount(...)`). Username is ignored —
colleagues type any user + the shared password. Browser caches the
credentials and resends `Authorization: Basic …` on every subsequent
request, so the prompt only appears once per browser session.

Empty `SHARED_PASSWORD` = closed (every request 401). No accidental
"left auth disabled in prod" mode.
"""

from __future__ import annotations

import base64
import secrets

from fastapi import status
from fastapi.responses import Response
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from . import config

_UNAUTHORIZED = Response(
    status_code=status.HTTP_401_UNAUTHORIZED,
    headers={"WWW-Authenticate": 'Basic realm="telemetry-chat"'},
)


def _password_matches(auth_header: str) -> bool:
    expected = config.SHARED_PASSWORD.encode("utf-8")
    if not expected or not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
        _, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return secrets.compare_digest(password.encode("utf-8"), expected)


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `lifespan` passes through (startup/shutdown have no auth). Everything
        # else — `http`, `websocket`, or unknown — must present credentials.
        # Browsers can't send Basic auth on a WS upgrade, so any WS attempt
        # will 401 here; we have no WS routes today, so this is fine.
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        auth = Headers(scope=scope).get("authorization", "")
        if not _password_matches(auth):
            await _UNAUTHORIZED(scope, receive, send)
            return
        await self.app(scope, receive, send)
