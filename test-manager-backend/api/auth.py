import os
from functools import lru_cache
from typing import TYPE_CHECKING, Callable, Literal, Optional

from fastapi import Depends, Header, HTTPException

from .settings import Settings, get_settings

if TYPE_CHECKING:
    from quixportal.auth import Auth


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
        auth_instance = Depends(auth),
        settings: Settings = Depends(get_settings),
        authorization: Optional[str] = Header(default=None),
    ) -> None:
        if not settings.api_auth_active:
            return None

        if authorization is None:
            raise HTTPException(status_code=403, detail="Not Allowed")

        if authorization.startswith(("bearer ", "Bearer ")):
            token = authorization[7:]
        else:
            token = authorization

        if not auth_instance.validate_permissions(
            token, "Workspace", settings.workspace_id, permission
        ):
            raise HTTPException(status_code=403, detail="Not Allowed")

        return None

    return inner


update_permission = validate_token("Update")
read_permission = validate_token("Read")
