"""
User profile routes
"""

import httpx
import os
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from ..auth import read_permission

router = APIRouter(prefix="/user", tags=["user"])


class UserProfile(BaseModel):
    """User profile model"""

    user_id: str
    name: str
    email: str


@router.get("/me", response_model=UserProfile)
async def get_current_user(
    authorization: str = Header(...),
    _auth: None = Depends(read_permission),
) -> UserProfile:
    """
    Get current user profile from Quix Portal API.

    Requires authentication. Uses the existing auth token to fetch
    user information from Portal API.
    """
    # Get Portal API URL from environment (server-side only)
    portal_api_url = os.getenv("Quix__Portal__Api")

    if not portal_api_url:
        # Portal API not available (not running in Quix Cloud)
        # Return a placeholder user
        return UserProfile(
            user_id="local",
            name="Authenticated User",
            email="",
        )

    # Extract token from Authorization header
    if authorization.startswith(("bearer ", "Bearer ")):
        token = authorization[7:]
    else:
        token = authorization

    try:
        # Call Portal API to get user profile
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{portal_api_url}/profile",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=10.0,
            )

            if not response.is_success:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Portal API error: {response.status_code}",
                )

            data = response.json()

            # Combine firstName and lastName into full name
            first_name = data.get("firstName") or ""
            last_name = data.get("lastName") or ""
            full_name = f"{first_name} {last_name}".strip() or "Authenticated User"

            return UserProfile(
                user_id=data.get("userId") or "unknown",
                name=full_name,
                email=data.get("email") or "",
            )

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Portal API timeout")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"Portal API error: {str(e)}")
