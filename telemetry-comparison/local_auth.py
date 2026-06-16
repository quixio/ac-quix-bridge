"""Local-development auth mock — mirrors `quixportal.auth.Auth` shape but
grants every permission. Activated by `LOCAL_DEV_MODE=true`.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)


class LocalAuth:
    def __init__(self) -> None:
        logger.info("Local Auth: Using mock authentication (all permissions granted)")

    def validate_permissions(
        self,
        token: str,
        resource_type: Literal["Workspace"] | str,
        resource_id: str,
        permission: Literal["Read", "Update"] | str,
    ) -> bool:
        logger.debug("Local Auth: %s/%s/%s -> GRANTED", resource_type, resource_id, permission)
        return True
