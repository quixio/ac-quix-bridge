import logging
import os
from functools import lru_cache
from typing import Callable, Literal, Optional

from fastapi import Depends, Header, HTTPException, Request

from .settings import Settings, get_settings

logger = logging.getLogger(__name__)


def extract_token(authorization: str | None) -> str | None:
    """Pull the token out of an Authorization header value.

    Accepts a `Bearer `/`bearer ` prefix or a bare token (Quix Portal embeds a
    bearer; the standalone PAT path may send it raw). Returns None when absent.
    """
    if not authorization:
        return None
    if authorization.startswith(("bearer ", "Bearer ")):
        return authorization[7:]
    return authorization


def bearer_from_request(request: Request) -> str | None:
    """The caller's token from the request Authorization header, or None.

    This is the same bearer Quix Portal injects into the embedded iframe (and
    that `validate_token` already authorizes against). Used to forward the
    user's identity to the post-race AI runner for attribution.
    """
    return extract_token(request.headers.get("authorization"))


def _token_preview(token: str) -> str:
    """Return a safe preview of a token: prefix + last 4 chars + length."""
    if not token:
        return "<empty>"
    if len(token) <= 12:
        return f"<short:{len(token)}>"
    return f"{token[:6]}...{token[-4:]} (len={len(token)})"


def _get_auth_implementation():
    """
    Get auth implementation based on environment.

    Returns LocalAuth for local development, or Quix Portal Auth for production.
    """
    if os.getenv("LOCAL_DEV_MODE") == "true":
        from .local_auth import LocalAuth

        return LocalAuth()
    else:
        from quixportal.auth import Auth

        return Auth()


@lru_cache(maxsize=1)
def auth():
    """Get cached auth implementation"""
    return _get_auth_implementation()


def validate_token(
    permission: Literal["Read", "Update"],
) -> Callable[..., None]:
    def inner(
        request: Request,
        auth_instance=Depends(auth),
        settings: Settings = Depends(get_settings),
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        if not settings.api_auth_active:
            return None

        path = request.url.path

        if authorization is None:
            logger.warning(
                "[auth] %s %s — REJECTED: no Authorization header", permission, path
            )
            raise HTTPException(status_code=403, detail="Not Allowed")

        token = extract_token(authorization) or ""
        scheme = "Bearer" if authorization.startswith(("bearer ", "Bearer ")) else "raw"

        ok = auth_instance.validate_permissions(
            token, "Workspace", settings.workspace_id, permission
        )
        if not ok:
            logger.warning(
                "[auth] %s %s — REJECTED: invalid token (scheme=%s, token=%s)",
                permission,
                path,
                scheme,
                _token_preview(token),
            )
            raise HTTPException(status_code=403, detail="Not Allowed")

        logger.debug(
            "[auth] %s %s — OK (scheme=%s, token=%s)",
            permission,
            path,
            scheme,
            _token_preview(token),
        )
        return None

    return inner


update_permission = validate_token("Update")
read_permission = validate_token("Read")
