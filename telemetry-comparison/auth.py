"""Bearer-token auth gate for API routes.

Validates `Authorization: Bearer <token>` against Quix Portal via the
`quixportal` SDK, scoped to this workspace. Public paths (the SPA shell,
static assets, health probe) bypass the gate so the frontend can boot
and run its token handshake before making any /api/* call.

Bypass switches:
- `LOCAL_DEV_MODE=true` → `LocalAuth` mock (all-grant)
- `API_AUTH_ACTIVE=false` → middleware no-op
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

import config

logger = logging.getLogger(__name__)

# Routes served before the frontend has obtained a token. Everything else
# (i.e. /api/*) requires a valid Bearer token. `/api/video/` is public
# because `<video src>` / `<img src>` element loads bypass `window.fetch` —
# the browser does not auto-attach an Authorization header on media-element
# range requests, so a gated route would 401. The data is replay footage of
# already-recorded laps; treat as low-sensitivity. Tighten with a query-
# param token if the videos become sensitive.
_PUBLIC_PATHS: tuple[str, ...] = ("/", "/health", "/favicon.ico", "/mcp")
# `/mcp/` is exempt from the Bearer gate — the mounted MCP sub-app enforces its
# own X-API-Key (see mcp_server._ApiKeyMiddleware); the agent has no Bearer.
# Use the trailing-slash prefix (+ exact `/mcp` above) so an unrelated future
# route like `/mcpanything` does NOT inherit the exemption.
_PUBLIC_PREFIXES: tuple[str, ...] = ("/static/", "/api/video/", "/mcp/")


def _token_preview(token: str) -> str:
    if not token:
        return "<empty>"
    if len(token) <= 12:
        return f"<short:{len(token)}>"
    return f"{token[:6]}...{token[-4:]} (len={len(token)})"


@lru_cache(maxsize=1)
def _auth_impl() -> Any:
    if config.LOCAL_DEV_MODE:
        from local_auth import LocalAuth

        return LocalAuth()
    from quixportal.auth import Auth

    return Auth()


def _extract_bearer(auth_header: str) -> str | None:
    """Return the token from an `Authorization: Bearer <t>` header, else None.
    Other schemes (Basic, Digest, etc.) and raw values are rejected so we
    don't forward arbitrary header content to the Portal SDK.
    """
    if not auth_header:
        return None
    if auth_header.startswith(("Bearer ", "bearer ")):
        return auth_header[7:]
    return None


def _is_public(path: str) -> bool:
    if path in _PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PREFIXES)


async def _send_json_error(send: Send, status_code: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b'Bearer realm="telemetry-explorer"'),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return

        if not config.API_AUTH_ACTIVE:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if _is_public(path):
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        token = _extract_bearer(headers.get("authorization", ""))
        if token is None:
            logger.info("[auth] %s — REJECTED: missing or non-Bearer Authorization", path)
            await _send_json_error(send, 401, "Not Authenticated")
            return

        try:
            ok = _auth_impl().validate_permissions(token, "Workspace", config.WORKSPACE_ID, "Read")
        except Exception:
            # Portal SDK / network failure — not a bad token. Surface as 503
            # so callers can retry instead of dropping the session.
            logger.exception("[auth] %s — token validation raised", path)
            await _send_json_error(send, 503, "Auth service unavailable")
            return

        if not ok:
            logger.info(
                "[auth] %s — REJECTED: invalid token (token=%s)",
                path,
                _token_preview(token),
            )
            await _send_json_error(send, 401, "Not Authenticated")
            return

        logger.debug("[auth] %s — OK (token_len=%d)", path, len(token))
        await self.app(scope, receive, send)
