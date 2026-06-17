"""
Integrations routes for external services
"""

import httpx
import logging
import os
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from ..auth import read_permission
from ..settings import get_settings
from .settings import get_effective_integration_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


class ConfigManagerUrl(BaseModel):
    """Configuration Manager URL response"""

    url: str


@router.get("/config-manager-frontend-url", response_model=ConfigManagerUrl)
async def get_config_manager_frontend_url(
    config_id: str | None = Query(
        None, description="Optional config ID for context-aware filtering"
    ),
    config_version: int | None = Query(None, description="Optional config version"),
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> ConfigManagerUrl:
    """
    Get direct frontend URL for Configuration Manager (for iframe embedding).

    Returns the direct frontend URL, checking stored settings first,
    then falling back to Portal API search.
    If config_id and config_version are provided, appends the details path.

    Args:
        config_id: Optional configuration ID to view specific config
        config_version: Optional configuration version
        authorization: Bearer token from request header

    Returns:
        ConfigManagerUrl with the direct frontend URL
    """
    # Check stored/effective settings first
    integration_settings = get_effective_integration_settings()
    if integration_settings.config_api_deployment:
        dep = integration_settings.config_api_deployment
        frontend_url = dep.embedded_view_url or dep.public_url
        if frontend_url:
            if config_id and config_version is not None:
                frontend_url += (
                    f"/details/{config_id}?version={config_version}&isIframe=true"
                )
            else:
                frontend_url += "?isIframe=true"
            return ConfigManagerUrl(url=frontend_url)

    # Fallback: search Portal API for deployment
    portal_api_url = os.getenv("Quix__Portal__Api")
    settings = get_settings()
    workspace_id = settings.workspace_id

    if not portal_api_url or not workspace_id:
        # Local development fallback
        base_url = "http://localhost:8001"
        if config_id and config_version is not None:
            base_url += f"/details/{config_id}?version={config_version}"
        return ConfigManagerUrl(url=base_url)

    # Extract token from Authorization header
    if authorization.startswith(("bearer ", "Bearer ")):
        token = authorization[7:]
    else:
        token = authorization

    try:
        # Query Portal API for deployments
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{portal_api_url}/workspaces/{workspace_id}/deployments",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Version": "2.0",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )

            if not response.is_success:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Portal API error: {response.status_code}",
                )

            deployments = response.json()

            # Find Dynamic Configuration Manager deployment
            config_manager = next(
                (
                    d
                    for d in deployments
                    if d.get("name") == "Dynamic Configuration Manager"
                ),
                None,
            )

            if not config_manager:
                raise HTTPException(
                    status_code=404,
                    detail="Dynamic Configuration Manager deployment not found",
                )

            # Get the direct frontend URL from plugin.embeddedViewUrl
            frontend_url = config_manager.get("plugin", {}).get("embeddedViewUrl")
            if not frontend_url:
                raise HTTPException(
                    status_code=500,
                    detail="Frontend URL (plugin.embeddedViewUrl) not found in deployment",
                )

            # Append context path if config_id provided
            if config_id and config_version is not None:
                frontend_url += (
                    f"/details/{config_id}?version={config_version}&isIframe=true"
                )
            else:
                frontend_url += "?isIframe=true"

            return ConfigManagerUrl(url=frontend_url)

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Portal API timeout")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Portal API error: {str(e)}")
