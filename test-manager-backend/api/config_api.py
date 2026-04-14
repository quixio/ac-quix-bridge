import httpx
from fastapi import Depends

from .settings import Settings, get_settings


def get_config_api_client(settings: Settings = Depends(get_settings)) -> httpx.Client:
    return httpx.Client(
        base_url=settings.config_api_url,
        headers={"Authorization": f"Bearer {settings.sdk_token}"},
    )
