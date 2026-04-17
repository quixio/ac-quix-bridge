import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING, Callable, Literal, Optional

from fastapi import Depends, Header, HTTPException, Request

from .settings import Settings, get_settings

if TYPE_CHECKING:
    from quixportal.auth import Auth

logger = logging.getLogger(__name__)


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
        auth_instance = Depends(auth),
        settings: Settings = Depends(get_settings),
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        if not settings.api_auth_active:
            return None

        path = request.url.path

        if authorization is None:
            logger.info("[auth] %s %s — REJECTED: no Authorization header", permission, path)
            raise HTTPException(status_code=403, detail="Not Allowed")

        if authorization.startswith(("bearer ", "Bearer ")):
            token = authorization[7:]
            scheme = "Bearer"
        else:
            token = authorization
            scheme = "raw"

        ok = auth_instance.validate_permissions(
            token, "Workspace", settings.workspace_id, permission
        )
        if not ok:
            logger.info(
                "[auth] %s %s — REJECTED: invalid token (scheme=%s, token=%s)",
                permission, path, scheme, _token_preview(token),
            )
            raise HTTPException(status_code=403, detail="Not Allowed")

        logger.info(
            "[auth] %s %s — OK (scheme=%s, token=%s)",
            permission, path, scheme, _token_preview(token),
        )
        return None

    return inner


update_permission = validate_token("Update")
read_permission = validate_token("Read")
