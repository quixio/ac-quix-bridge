"""
Application settings routes for managing integration configurations.
"""

import httpx
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from ..auth import read_permission, update_permission
from ..models import (
    IntegrationSettings,
    IntegrationSettingsUpdate,
    Topic,
    Workspace,
    DeploymentReference,
    TopicReference,
)
from ..mongo import get_mongo
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

# Collection name for app settings
SETTINGS_COLLECTION = "app_settings"
SETTINGS_DOC_ID = "integration_settings"


def get_db_settings() -> IntegrationSettings | None:
    """Get integration settings from MongoDB."""
    mongo = get_mongo()
    doc = mongo[SETTINGS_COLLECTION].find_one({"_id": SETTINGS_DOC_ID})
    if doc:
        doc.pop("_id", None)
        return IntegrationSettings(**doc)
    return None


def get_effective_integration_settings() -> IntegrationSettings:
    """
    Get effective integration settings with priority:
    1. MongoDB stored values (highest priority)
    2. Environment variables for topic fallback
    3. Default values (lowest priority)
    """
    env_settings = get_settings()
    db_settings = get_db_settings()

    # Start with empty effective settings
    effective = IntegrationSettings()

    # Apply environment variable fallbacks for topic if no DB settings
    if env_settings.measurements_topic_name:
        effective.measurements_topic = TopicReference(
            topic_name=env_settings.measurements_topic_name,
            workspace_id=env_settings.measurements_workspace_id
            or env_settings.workspace_id,
            workspace_name=None,
        )

    # Override with DB settings if they exist and have values
    if db_settings:
        # Config API deployment
        if db_settings.config_api_deployment:
            effective.config_api_deployment = db_settings.config_api_deployment

        # Measurements - deployment and topic
        if db_settings.measurements_deployment:
            effective.measurements_deployment = db_settings.measurements_deployment
        if db_settings.measurements_topic:
            effective.measurements_topic = db_settings.measurements_topic

        # Analytics deployment
        if db_settings.analytics_deployment:
            effective.analytics_deployment = db_settings.analytics_deployment

        effective.updated_at = db_settings.updated_at
        effective.updated_by = db_settings.updated_by

    return effective


async def apply_fallback_deployments(
    settings: IntegrationSettings,
    authorization: str,
) -> IntegrationSettings:
    """
    Apply fallback deployment logic for settings without explicit deployment selection.

    Searches the current workspace for:
    - "Dynamic Configuration Manager" for config_api_deployment
    - "Query UI" for measurements_deployment
    - "Marimo" or "Analytics" for analytics_deployment
    """
    from .portal import get_fallback_deployment

    app_settings = get_settings()

    # Config API fallback - search for "Dynamic Configuration Manager"
    if not settings.config_api_deployment:
        try:
            fallback = await get_fallback_deployment(
                deployment_name="Dynamic Configuration Manager",
                authorization=authorization,
                _auth=None,
            )

            if fallback:
                settings.config_api_deployment = DeploymentReference(
                    deployment_id=fallback.deployment_id,
                    workspace_id=app_settings.workspace_id or "",
                    deployment_name=fallback.name,
                    public_url=fallback.public_url,
                    embedded_view_url=fallback.embedded_view_url,
                    internal_url=f"http://{fallback.service_name}"
                    if fallback.service_name
                    else fallback.public_url,
                )
                settings.config_api_is_fallback = True

        except Exception as e:
            logger.warning(f"Failed to get Config API fallback deployment: {e}")

    # Measurements (Query UI) fallback - search for "Query UI"
    if not settings.measurements_deployment:
        try:
            fallback = await get_fallback_deployment(
                deployment_name="Query UI",
                authorization=authorization,
                _auth=None,
            )

            if fallback:
                settings.measurements_deployment = DeploymentReference(
                    deployment_id=fallback.deployment_id,
                    workspace_id=app_settings.workspace_id or "",
                    deployment_name=fallback.name,
                    public_url=fallback.public_url,
                    embedded_view_url=fallback.embedded_view_url,
                    internal_url=f"http://{fallback.service_name}"
                    if fallback.service_name
                    else fallback.public_url,
                )
                settings.measurements_is_fallback = True

        except Exception as e:
            logger.warning(f"Failed to get Measurements fallback deployment: {e}")

    # Measurements topic fallback - use env var if not set
    if not settings.measurements_topic and app_settings.measurements_topic_name:
        settings.measurements_topic = TopicReference(
            topic_name=app_settings.measurements_topic_name,
            workspace_id=app_settings.measurements_workspace_id
            or app_settings.workspace_id,
            workspace_name=None,
        )

    # Analytics fallback - search for "Marimo" first, then "Analytics"
    if not settings.analytics_deployment:
        try:
            # Try "Marimo" first
            fallback = await get_fallback_deployment(
                deployment_name="Marimo",
                authorization=authorization,
                _auth=None,
            )

            # If not found, try "Analytics"
            if not fallback:
                fallback = await get_fallback_deployment(
                    deployment_name="Analytics",
                    authorization=authorization,
                    _auth=None,
                )

            if fallback:
                settings.analytics_deployment = DeploymentReference(
                    deployment_id=fallback.deployment_id,
                    workspace_id=app_settings.workspace_id or "",
                    deployment_name=fallback.name,
                    public_url=fallback.public_url,
                    embedded_view_url=fallback.embedded_view_url,
                    internal_url=f"http://{fallback.service_name}"
                    if fallback.service_name
                    else fallback.public_url,
                )
                settings.analytics_is_fallback = True

        except Exception as e:
            logger.warning(f"Failed to get Analytics fallback deployment: {e}")

    return settings


@router.get("", response_model=IntegrationSettings)
async def get_integration_settings(
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> IntegrationSettings:
    """
    Get current integration settings.

    Returns the effective settings with priority:
    1. User-configured values from MongoDB (highest)
    2. Fallback deployments from current workspace
    3. Environment variables
    4. Default values (lowest)
    """
    settings = get_effective_integration_settings()

    # Apply fallback deployment logic
    settings = await apply_fallback_deployments(settings, authorization)

    return settings


@router.put("", response_model=IntegrationSettings)
async def update_integration_settings(
    settings_update: IntegrationSettingsUpdate,
    authorization: str = Header(...),
    _auth: None = Depends(update_permission),
) -> IntegrationSettings:
    """
    Update integration settings.

    Stores the provided settings in MongoDB. These settings will override
    environment variables when the application reads configuration.
    """
    mongo = get_mongo()

    # Get current user for audit
    user_name = "Unknown"
    portal_api_url = os.getenv("Quix__Portal__Api")
    if portal_api_url:
        if authorization.startswith(("bearer ", "Bearer ")):
            token = authorization[7:]
        else:
            token = authorization

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{portal_api_url}/profile",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=5.0,
                )
                if response.is_success:
                    data = response.json()
                    first_name = data.get("firstName") or ""
                    last_name = data.get("lastName") or ""
                    user_name = f"{first_name} {last_name}".strip() or "Unknown"
        except Exception as e:
            logger.warning(f"Failed to get user profile: {e}")

    # Build the update document - use exclude_unset to preserve explicit null values
    update_data = settings_update.model_dump(exclude_unset=True)

    # Separate null values for $unset and non-null for $set
    unset_fields = {k: "" for k, v in update_data.items() if v is None}
    set_fields = {k: v for k, v in update_data.items() if v is not None}
    set_fields["updated_at"] = datetime.now(timezone.utc)
    set_fields["updated_by"] = user_name

    update_ops: dict = {"$set": set_fields}
    if unset_fields:
        update_ops["$unset"] = unset_fields

    # Upsert the settings document
    mongo[SETTINGS_COLLECTION].update_one(
        {"_id": SETTINGS_DOC_ID},
        update_ops,
        upsert=True,
    )

    # Return effective settings (no fallback applied on explicit save)
    return get_effective_integration_settings()


@router.get("/topics", response_model=list[Topic])
async def get_topics(
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> list[Topic]:
    """
    Get available topics from Portal API.

    In local development mode, returns a mock list of topics.
    """
    portal_api_url = os.getenv("Quix__Portal__Api")
    settings = get_settings()
    workspace_id = settings.workspace_id

    # Local development fallback
    if not portal_api_url or workspace_id == "local-dev-workspace":
        return [
            Topic(id="mock-topic-1", name="mock-topic-1"),
            Topic(id="mock-topic-2", name="mock-topic-2"),
            Topic(id="test-measurements", name="test-measurements"),
            Topic(id="tsbs_data_transformed", name="tsbs_data_transformed"),
        ]

    # Extract token from Authorization header
    if authorization.startswith(("bearer ", "Bearer ")):
        token = authorization[7:]
    else:
        token = authorization

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{portal_api_url}/workspaces/{workspace_id}/topics",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Version": "2.0",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )

            if not response.is_success:
                logger.warning(
                    f"Portal API error fetching topics: {response.status_code}"
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Portal API error: {response.status_code}",
                )

            data = response.json()

            # Portal API returns topics in various formats, handle common ones
            topics = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        topic_id = (
                            item.get("id") or item.get("name") or item.get("topicId")
                        )
                        topic_name = (
                            item.get("name") or item.get("id") or item.get("topicName")
                        )
                        if topic_id and topic_name:
                            topics.append(Topic(id=topic_id, name=topic_name))
                    elif isinstance(item, str):
                        topics.append(Topic(id=item, name=item))

            return topics

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Portal API timeout")
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching topics: {e}")
        raise HTTPException(status_code=500, detail=f"Portal API error: {str(e)}")


@router.get("/workspaces", response_model=list[Workspace])
async def get_workspaces(
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> list[Workspace]:
    """
    Get available workspaces from Portal API.

    In local development mode, returns a mock list of workspaces.
    """
    portal_api_url = os.getenv("Quix__Portal__Api")
    settings = get_settings()
    workspace_id = settings.workspace_id

    # Local development fallback
    if not portal_api_url or workspace_id == "local-dev-workspace":
        return [
            Workspace(id="local-dev-workspace", name="Test Manager Dev"),
            Workspace(id="mock-data-lake-workspace", name="Data Lake Production"),
        ]

    # Extract token from Authorization header
    if authorization.startswith(("bearer ", "Bearer ")):
        token = authorization[7:]
    else:
        token = authorization

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{portal_api_url}/workspaces",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Version": "2.0",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )

            if not response.is_success:
                logger.warning(
                    f"Portal API error fetching workspaces: {response.status_code}"
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Portal API error: {response.status_code}",
                )

            data = response.json()

            # Portal API returns workspaces in various formats, handle common ones
            workspaces = []
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        ws_id = item.get("workspaceId") or item.get("id")
                        ws_name = item.get("name") or ws_id
                        if ws_id:
                            workspaces.append(Workspace(id=ws_id, name=ws_name))

            return workspaces

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Portal API timeout")
    except httpx.HTTPError as e:
        logger.error(f"HTTP error fetching workspaces: {e}")
        raise HTTPException(status_code=500, detail=f"Portal API error: {str(e)}")
