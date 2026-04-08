"""
Portal API proxy routes for fetching repositories, workspaces, and deployments.
"""

import httpx
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from ..auth import read_permission
from ..models import Repository, WorkspaceDetails, DeploymentInfo, TopicInfo
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal", tags=["portal"])


def extract_token(authorization: str) -> str:
    """Extract bearer token from Authorization header."""
    if authorization.startswith(("bearer ", "Bearer ")):
        return authorization[7:]
    return authorization


def get_portal_api_url() -> str | None:
    """Get Portal API URL from environment."""
    return os.getenv("Quix__Portal__Api")


async def portal_get(endpoint: str, token: str, params: dict | None = None, version: str = "2.0") -> Any:
    """Make GET request to Portal API."""
    portal_api_url = get_portal_api_url()
    if not portal_api_url:
        raise HTTPException(
            status_code=503,
            detail="Portal API not configured"
        )

    try:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if version:
            headers["X-Version"] = version

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{portal_api_url}{endpoint}",
                params=params,
                headers=headers,
                timeout=15.0,
            )

            if not response.is_success:
                logger.warning(f"Portal API error: {response.status_code} for {endpoint}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Portal API error: {response.status_code}",
                )

            return response.json()

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Portal API timeout")
    except httpx.HTTPError as e:
        logger.error(f"HTTP error calling Portal API: {e}")
        raise HTTPException(status_code=500, detail=f"Portal API error: {str(e)}")


@router.get("/repositories", response_model=list[Repository])
async def get_repositories(
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> list[Repository]:
    """
    Get available repositories (projects) from Portal API.

    In local development mode, returns mock data.
    """
    portal_api_url = get_portal_api_url()
    settings = get_settings()

    # Local development fallback
    if not portal_api_url or settings.workspace_id == "local-dev-workspace":
        return [
            Repository(repositoryId="repo-test-manager", name="Test Manager"),
            Repository(repositoryId="repo-data-lake", name="Data Lake"),
            Repository(repositoryId="repo-analytics", name="Analytics Platform"),
        ]

    token = extract_token(authorization)
    data = await portal_get("/repositories", token)

    repositories = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                repo_id = item.get("repositoryId") or item.get("id")
                name = item.get("name") or repo_id
                if repo_id:
                    repositories.append(Repository(repositoryId=repo_id, name=name))

    return repositories


@router.get("/workspaces", response_model=list[WorkspaceDetails])
async def get_workspaces_details(
    repository_id: str | None = Query(None, description="Filter by repository/project ID"),
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> list[WorkspaceDetails]:
    """
    Get workspaces/environments with details from Portal API.

    Can optionally filter by repository_id to get only environments from a specific project.
    In local development mode, returns mock data.
    """
    portal_api_url = get_portal_api_url()
    settings = get_settings()

    # Local development fallback
    if not portal_api_url or settings.workspace_id == "local-dev-workspace":
        mock_workspaces = [
            WorkspaceDetails(
                workspaceId="local-dev-workspace",
                name="Test Manager Dev",
                repositoryId="repo-test-manager",
                environmentName="dev",
                status="Ready",
            ),
            WorkspaceDetails(
                workspaceId="testmanager-prod",
                name="Test Manager Prod",
                repositoryId="repo-test-manager",
                environmentName="prod",
                status="Ready",
            ),
            WorkspaceDetails(
                workspaceId="datalake-prod",
                name="Data Lake Prod",
                repositoryId="repo-data-lake",
                environmentName="prod",
                status="Ready",
            ),
        ]

        if repository_id:
            return [w for w in mock_workspaces if w.repository_id == repository_id]
        return mock_workspaces

    token = extract_token(authorization)
    data = await portal_get("/workspaces", token)

    workspaces = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                ws_id = item.get("workspaceId") or item.get("id")
                repo_id = item.get("repositoryId") or item.get("repository", {}).get("repositoryId")
                env_name = item.get("environmentName") or item.get("name") or "default"
                name = item.get("name") or ws_id
                status = item.get("status") or "Unknown"

                # Skip if filtering by repository and doesn't match
                if repository_id and repo_id != repository_id:
                    continue

                if ws_id:
                    workspaces.append(WorkspaceDetails(
                        workspaceId=ws_id,
                        name=name,
                        repositoryId=repo_id or "",
                        environmentName=env_name,
                        status=status,
                    ))

    return workspaces


@router.get("/workspaces/{workspace_id}/deployments", response_model=list[DeploymentInfo])
async def get_deployments(
    workspace_id: str,
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> list[DeploymentInfo]:
    """
    Get deployments for a specific workspace from Portal API.

    Returns deployment information including public URLs and embedded view URLs.
    In local development mode, returns mock data.
    """
    portal_api_url = get_portal_api_url()
    settings = get_settings()

    # Local development fallback
    if not portal_api_url or settings.workspace_id == "local-dev-workspace":
        return [
            DeploymentInfo(
                deploymentId="deploy-config-manager",
                name="Dynamic Configuration Manager",
                status="Running",
                publicUrl="http://config-api:8001",
                embedded_view_url=None,
                service_name="dynamic-configuration-manager",
                publicAccess=True,
            ),
            DeploymentInfo(
                deploymentId="deploy-query-ui",
                name="Query UI",
                status="Running",
                publicUrl="http://query-ui.app.quix.io",
                embedded_view_url="http://query-ui.app.quix.io/embed",
                service_name="query-ui",
                publicAccess=True,
            ),
            DeploymentInfo(
                deploymentId="deploy-marimo",
                name="Marimo Analytics",
                status="Running",
                publicUrl="http://marimo.app.quix.io",
                embedded_view_url=None,
                service_name="marimo",
                publicAccess=True,
            ),
        ]

    token = extract_token(authorization)
    data = await portal_get(f"/workspaces/{workspace_id}/deployments", token)

    deployments = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                deploy_id = item.get("deploymentId") or item.get("id")
                name = item.get("name") or deploy_id
                status = item.get("status") or "Unknown"

                # Extract URLs from various possible locations
                public_url = item.get("publicUrl")
                embedded_view_url = None
                service_name = None
                public_access = item.get("publicAccess", False)

                # Check network settings
                network = item.get("network", {})
                if network:
                    service_name = network.get("serviceName")

                # Check plugin settings for embedded view
                plugin = item.get("plugin", {})
                if plugin:
                    embedded_view_url = plugin.get("embeddedViewUrl")

                # Build internal URL from service name if available
                if not public_url and service_name:
                    public_url = f"http://{service_name}"

                if deploy_id:
                    deployments.append(DeploymentInfo(
                        deploymentId=deploy_id,
                        name=name,
                        status=status,
                        publicUrl=public_url,
                        embedded_view_url=embedded_view_url,
                        service_name=service_name,
                        publicAccess=public_access,
                    ))

    return deployments


@router.get("/current-workspace-id")
async def get_current_workspace_id(
    _auth: None = Depends(read_permission),
) -> dict[str, str]:
    """
    Get the current workspace ID from environment.

    Used by frontend to detect which workspace we're running in for fallback logic.
    """
    settings = get_settings()
    return {"workspace_id": settings.workspace_id or ""}


@router.get("/fallback-deployment", response_model=DeploymentInfo | None)
async def get_fallback_deployment(
    deployment_name: str = Query(
        default="Dynamic Configuration Manager",
        description="Name of the deployment to search for",
    ),
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> DeploymentInfo | None:
    """
    Search for a deployment by name in the current workspace.

    Used to find fallback deployments like "Dynamic Configuration Manager"
    when no explicit selection has been made.
    """
    settings = get_settings()
    workspace_id = settings.workspace_id

    if not workspace_id:
        return None

    # Get all deployments in current workspace
    try:
        deployments = await get_deployments(
            workspace_id=workspace_id,
            authorization=authorization,
            _auth=None,
        )

        # Search for deployment by name (case-insensitive partial match)
        search_name = deployment_name.lower()
        for deployment in deployments:
            if search_name in deployment.name.lower():
                return deployment

        return None

    except HTTPException:
        return None
    except Exception as e:
        logger.warning(f"Error searching for fallback deployment: {e}")
        return None


@router.get("/workspaces/{workspace_id}/topics", response_model=list[TopicInfo])
async def get_workspace_topics(
    workspace_id: str,
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> list[TopicInfo]:
    """
    Get topics for a specific workspace from Portal API.

    Returns topic information including name and status.
    In local development mode, returns mock data.
    """
    portal_api_url = get_portal_api_url()
    settings = get_settings()

    # Local development fallback
    if not portal_api_url or settings.workspace_id == "local-dev-workspace":
        return [
            TopicInfo(
                topicId="topic-1",
                name="tsbs_data_transformed",
                workspaceId=workspace_id,
                status="Ready",
            ),
            TopicInfo(
                topicId="topic-2",
                name="test-measurements",
                workspaceId=workspace_id,
                status="Ready",
            ),
            TopicInfo(
                topicId="topic-3",
                name="raw-sensor-data",
                workspaceId=workspace_id,
                status="Ready",
            ),
        ]

    token = extract_token(authorization)
    data = await portal_get(f"/{workspace_id}/topics", token, version="1.0")

    topics = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                topic_id = item.get("id") or item.get("topicId") or item.get("name")
                name = item.get("name") or item.get("topicName") or topic_id
                status = item.get("status")

                if topic_id and name:
                    topics.append(TopicInfo(
                        topicId=topic_id,
                        name=name,
                        workspaceId=workspace_id,
                        status=status,
                    ))
            elif isinstance(item, str):
                # Simple string topic names
                topics.append(TopicInfo(
                    topicId=item,
                    name=item,
                    workspaceId=workspace_id,
                    status=None,
                ))

    return topics
