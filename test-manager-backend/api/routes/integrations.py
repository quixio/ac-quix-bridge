"""
Integrations routes for external services
"""

import httpx
import logging
import os
from urllib.parse import quote
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


def get_measurements_url_base(integration_settings) -> str | None:
    """Get the base URL for measurements service from deployment reference."""
    if not integration_settings.measurements_deployment:
        return None
    dep = integration_settings.measurements_deployment
    # Prefer public_url or embedded_view_url for UI access
    return dep.public_url or dep.embedded_view_url


@router.get("/measurements-url", response_model=ConfigManagerUrl)
async def get_measurements_url(
    test_id: str | None = Query(None, description="Test ID for SQL filter"),
    campaign_id: str | None = Query(None, description="Campaign ID for SQL filter"),
    environment_id: str | None = Query(
        None, description="Environment ID for SQL filter"
    ),
    _auth: None = Depends(read_permission),
) -> ConfigManagerUrl:
    """
    Get Measurements/Query Builder URL.

    Returns Query Builder URL with pre-filled SQL query and authentication token.
    SQL query filters by test context (campaign_id, environment_id, test_id).
    """
    settings = get_settings()
    integration_settings = get_effective_integration_settings()

    # Get measurements URL from deployment
    measurements_url = get_measurements_url_base(integration_settings)
    if not measurements_url:
        raise HTTPException(
            status_code=501,
            detail="Measurements service not configured. Configure it in Settings.",
        )

    # Check if topic is configured
    if not integration_settings.measurements_topic:
        raise HTTPException(
            status_code=501,
            detail="Measurements topic not configured. Configure it in Settings.",
        )

    topic_name = integration_settings.measurements_topic.topic_name

    # Build SQL query with filters
    sql_parts = [f"SELECT * FROM {topic_name} WHERE 1=1"]
    if campaign_id:
        sql_parts.append(f"AND campaign_id = '{campaign_id}'")
    if environment_id:
        sql_parts.append(f"AND environment_id = '{environment_id}'")
    if test_id:
        sql_parts.append(f"AND test_id = '{test_id}'")
    sql_parts.append("LIMIT 100")

    sql_query = " ".join(sql_parts)
    encoded_sql = quote(sql_query)

    # Build URL with token and SQL
    url = f"{measurements_url}?token={settings.sdk_token}&sql={encoded_sql}"

    # Add autorun only if test_id exists (contextual mode)
    if test_id:
        url += "&autorun=true"

    return ConfigManagerUrl(url=url)


def get_analytics_url_base(integration_settings) -> str | None:
    """Get the base URL for analytics service from deployment reference."""
    if not integration_settings.analytics_deployment:
        return None
    dep = integration_settings.analytics_deployment
    # Prefer embedded_view_url for UI embedding, then public_url
    return dep.embedded_view_url or dep.public_url


@router.get("/analytics-url", response_model=ConfigManagerUrl)
async def get_analytics_url(
    test_id: str | None = Query(None, description="Test ID for context"),
    campaign_id: str | None = Query(None, description="Campaign ID for context"),
    environment_id: str | None = Query(None, description="Environment ID for context"),
    _auth: None = Depends(read_permission),
) -> ConfigManagerUrl:
    """
    Get Analytics/Notebook URL.

    Returns Notebook URL with authentication token and test context parameters.
    """
    settings = get_settings()
    integration_settings = get_effective_integration_settings()

    # Get analytics URL from deployment
    analytics_url = get_analytics_url_base(integration_settings)
    if not analytics_url:
        raise HTTPException(
            status_code=501,
            detail="Analytics service not configured. Configure it in Settings.",
        )

    # Build URL with token and context parameters
    url = f"{analytics_url}?token={settings.sdk_token}"

    if campaign_id:
        url += f"&campaign_id={campaign_id}"
    if environment_id:
        url += f"&environment_id={environment_id}"
    if test_id:
        url += f"&test_id={test_id}"

    return ConfigManagerUrl(url=url)
