from typing import Callable, TypeVar

import httpx
from fastapi import Depends, HTTPException

from .settings import Settings, get_settings


T = TypeVar("T")


def get_config_api_client(settings: Settings = Depends(get_settings)) -> httpx.Client:
    return httpx.Client(
        base_url=settings.config_api_url,
        headers={"Authorization": f"Bearer {settings.sdk_token}"},
    )


def safe_call(fn: Callable[[], T]) -> T:
    """Execute a DCM httpx call; translate network-level errors to HTTP 503.

    Catches httpx.RequestError (ConnectError, TimeoutException, ReadError, …).
    HTTPStatusError from DCM 4xx/5xx responses is NOT caught — callers decide
    how to handle those (typically map to 424 Failed Dependency).
    """
    try:
        return fn()
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Configuration service unavailable: {type(e).__name__}",
        )
