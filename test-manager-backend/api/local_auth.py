"""
Local Authentication Mock

This module provides a mock implementation of Quix Portal authentication
for local development. It mimics the interface of `quixportal.auth.Auth`
but always allows all operations.

This is only used when LOCAL_DEV_MODE=true environment variable is set.
"""

import logging
from typing import Literal

logger = logging.getLogger(__name__)


class LocalAuth:
    """
    Mock authentication class for local development.

    Mimics the interface of `quixportal.auth.Auth` from the quixportal package,
    but always returns True for all permission checks.
    """

    def __init__(self):
        """Initialize local auth mock"""
        logger.info("🔓 Local Auth: Using mock authentication (all permissions granted)")

    def validate_permissions(
        self,
        token: str,
        resource_type: Literal["Workspace"] | str,
        resource_id: str,
        permission: Literal["read", "write", "admin"] | str,
    ) -> bool:
        """
        Mock permission validation - always returns True.

        Args:
            token: Bearer token (ignored in local mode)
            resource_type: Type of resource (ignored)
            resource_id: ID of resource (ignored)
            permission: Permission level being requested (ignored)

        Returns:
            Always True (all permissions granted in local development)
        """
        logger.debug(
            f"Local Auth: Permission check - resource_type={resource_type}, "
            f"resource_id={resource_id}, permission={permission} -> GRANTED"
        )
        return True
