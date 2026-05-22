from contextlib import asynccontextmanager
import logging
import os
import socket
from collections.abc import Sequence
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import mongo
from .analysis_runner import cleanup_orphans
from .routes import mcp as mcp_router
from .routes.analyses import router as analyses_router
from .routes.devices import router as devices_router
from .routes.drivers import router as drivers_router
from .routes.environments import router as environments_router
from .routes.integrations import router as integrations_router
from .routes.leaderboard import router as leaderboard_router
from .routes.logbook import router as logbook_router
from .routes.portal import router as portal_router
from .routes.tests import router as tests_router
from .routes.user import router as user_router
from .routes.settings import router as settings_router
from .settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Handles startup and shutdown events for the application.
    Connects to MongoDB on startup and closes the connection on shutdown.
    """
    settings = get_settings()

    # Log startup mode
    local_dev_mode = os.getenv("LOCAL_DEV_MODE") == "true"
    if local_dev_mode:
        logger.info("=" * 60)
        logger.info("🐳 STARTING IN LOCAL DEVELOPMENT MODE")
        logger.info("=" * 60)
        logger.info("✓ Using local MongoDB and Config API")
        logger.info("✓ Using mock authentication (all requests allowed)")
        logger.info(
            f"✓ API authentication: {'DISABLED' if not settings.api_auth_active else 'ENABLED'}"
        )
        logger.info(f"✓ Config API: {settings.config_api_url}")
        logger.info("=" * 60)
    else:
        logger.info("=" * 60)
        logger.info("☁️  STARTING IN PRODUCTION MODE (Quix Cloud)")
        logger.info("=" * 60)
        logger.info(f"✓ Workspace ID: {settings.workspace_id}")
        logger.info(
            f"✓ API authentication: {'ENABLED' if settings.api_auth_active else 'DISABLED'}"
        )
        logger.info(f"✓ Config API: {settings.config_api_url}")
        logger.info("=" * 60)

    _probe_config_api(settings.config_api_url, settings.sdk_token)

    mongo.connect(settings.mongo)
    mcp_router.install(app, mongo=mongo.get_mongo())

    # Mark stuck non-terminal analyses as orphaned on every restart.
    cleanup_orphans(mongo.get_mongo())

    yield
    mongo.disconnect()


def _probe_config_api(url: str, sdk_token: str) -> None:
    """One-shot startup probe to verify the DCM URL is reachable."""
    try:
        host = url.split("://", 1)[-1].split("/")[0].split(":")[0]
        ip = socket.gethostbyname(host)
        logger.info("[probe] DNS %s → %s", host, ip)
    except Exception as e:
        logger.error("[probe] DNS FAILED for %s — %s", url, e)
        return

    try:
        with httpx.Client() as client:
            resp = client.get(
                f"{url}/api/v1/configurations",
                headers={"Authorization": f"Bearer {sdk_token}"} if sdk_token else {},
                timeout=5.0,
            )
        logger.info(
            "[probe] GET %s/api/v1/configurations → %d %s",
            url,
            resp.status_code,
            resp.text[:200],
        )
    except Exception as e:
        logger.error("[probe] /api/v1/configurations FAILED — %s", e)


def format_validation_error(errors: Sequence[Any]) -> str:
    """
    Transform Pydantic validation errors into user-friendly messages.

    Args:
        errors: List of Pydantic validation error dictionaries

    Returns:
        User-friendly error message string
    """
    if not errors:
        return "Validation error occurred"

    friendly_messages = []

    for error in errors:
        error_type = error.get("type", "")
        location = error.get("loc", [])
        msg = error.get("msg", "")

        # Extract field name (skip 'body' prefix if present)
        field_path = [str(loc) for loc in location if loc != "body"]
        field_name = ".".join(field_path) if field_path else "field"

        # Create user-friendly messages based on error type
        if error_type == "dict_type":
            if "sensors" in field_path:
                friendly_messages.append(
                    f"'{field_name}' must be a dictionary format like: "
                    '{"sensor1": {"type": "temperature", "unit": "C"}, "sensor2": {...}}'
                )
            else:
                friendly_messages.append(
                    f"'{field_name}' must be an object/dictionary, not a list or string"
                )

        elif error_type == "list_type":
            friendly_messages.append(f"'{field_name}' must be a list/array")

        elif error_type == "missing":
            friendly_messages.append(f"'{field_name}' is required but was not provided")

        elif error_type.startswith("enum"):
            # Extract allowed values if available in the message
            friendly_messages.append(f"'{field_name}' has an invalid value. {msg}")

        elif error_type in ["int_type", "float_type", "bool_type", "string_type"]:
            expected_type = error_type.replace("_type", "")
            friendly_messages.append(f"'{field_name}' must be a {expected_type}")

        elif error_type == "datetime_parsing":
            friendly_messages.append(
                f"'{field_name}' must be a valid datetime (ISO 8601 format)"
            )

        elif error_type == "value_error":
            friendly_messages.append(f"'{field_name}': {msg}")

        elif "too_short" in error_type:
            friendly_messages.append(f"'{field_name}' is too short. {msg}")

        elif "too_long" in error_type:
            friendly_messages.append(f"'{field_name}' is too long. {msg}")

        else:
            # Fallback to original message
            friendly_messages.append(f"'{field_name}': {msg}")

    return " | ".join(friendly_messages)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Custom exception handler for Pydantic validation errors.
    Transforms technical validation errors into user-friendly messages.
    """
    errors = exc.errors()
    friendly_message = format_validation_error(errors)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": friendly_message,
            "errors": errors,  # Keep original errors for debugging
        },
    )


def create_app() -> FastAPI:
    application = FastAPI(
        title="Test Manager API",
        docs_url="/",
        lifespan=lifespan,
    )

    # Register custom exception handler for validation errors.
    # ty flags the handler's param type (RequestValidationError) as narrower than
    # Starlette's ExceptionHandler signature — this is the documented FastAPI pattern.
    application.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )  # ty: ignore[invalid-argument-type]

    application.include_router(tests_router, tags=["tests"], prefix="/api/v1")
    application.include_router(devices_router, tags=["devices"], prefix="/api/v1")
    application.include_router(drivers_router, tags=["drivers"], prefix="/api/v1")
    application.include_router(
        environments_router, tags=["environments"], prefix="/api/v1"
    )
    application.include_router(logbook_router, tags=["logbook"], prefix="/api/v1")
    application.include_router(user_router, tags=["user"], prefix="/api/v1")
    application.include_router(
        integrations_router, tags=["integrations"], prefix="/api/v1"
    )
    application.include_router(portal_router, tags=["portal"], prefix="/api/v1")
    application.include_router(settings_router, tags=["settings"], prefix="/api/v1")
    application.include_router(
        leaderboard_router, tags=["leaderboard"], prefix="/api/v1"
    )
    application.include_router(analyses_router, tags=["analyses"], prefix="/api/v1")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


# Create app instance at module level for uvicorn hot reload
app = create_app()
