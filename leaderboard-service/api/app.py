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

from . import live_stream, live_telemetry, mongo
from .routes.leaderboard import router as leaderboard_router
from .routes.leaderboard_dropdowns import router as leaderboard_dropdowns_router
from .routes.leaderboard_stream import router as leaderboard_stream_router
from .settings import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    local_dev_mode = os.getenv("LOCAL_DEV_MODE") == "true"
    live_telemetry_enabled = os.getenv("LIVE_TELEMETRY_ENABLED", "true").lower() == "true"

    if local_dev_mode:
        logger.info("=" * 60)
        logger.info("STARTING IN LOCAL DEVELOPMENT MODE")
        logger.info("=" * 60)
        logger.info("Using local MongoDB and Config API")
        logger.info("Using mock authentication (all requests allowed)")
        logger.info(
            "API authentication: %s",
            "DISABLED" if not settings.api_auth_active else "ENABLED",
        )
        logger.info("Config API: %s", settings.config_api_url)
        logger.info("=" * 60)
    else:
        logger.info("=" * 60)
        logger.info("STARTING IN PRODUCTION MODE (Quix Cloud)")
        logger.info("=" * 60)
        logger.info("Workspace ID: %s", settings.workspace_id)
        logger.info(
            "API authentication: %s",
            "ENABLED" if settings.api_auth_active else "DISABLED",
        )
        logger.info("Config API: %s", settings.config_api_url)
        logger.info("=" * 60)

    logger.info("Lake table (LAKE_TABLE): %s", settings.lake_table)
    logger.info("LIVE_TELEMETRY_ENABLED: %s", live_telemetry_enabled)

    _probe_config_api(settings.config_api_url, settings.sdk_token)

    mongo.connect(settings.mongo)

    await live_stream.start_broadcaster()
    live_telemetry.start()

    yield
    live_telemetry.stop()
    await live_stream.stop_broadcaster()
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
    if not errors:
        return "Validation error occurred"

    friendly_messages = []

    for error in errors:
        error_type = error.get("type", "")
        location = error.get("loc", [])
        msg = error.get("msg", "")

        field_path = [str(loc) for loc in location if loc != "body"]
        field_name = ".".join(field_path) if field_path else "field"

        if error_type == "dict_type":
            friendly_messages.append(
                f"'{field_name}' must be an object/dictionary, not a list or string"
            )
        elif error_type == "list_type":
            friendly_messages.append(f"'{field_name}' must be a list/array")
        elif error_type == "missing":
            friendly_messages.append(f"'{field_name}' is required but was not provided")
        elif error_type.startswith("enum"):
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
            friendly_messages.append(f"'{field_name}': {msg}")

    return " | ".join(friendly_messages)


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    friendly_message = format_validation_error(errors)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": friendly_message,
            "errors": errors,
        },
    )


def create_app() -> FastAPI:
    application = FastAPI(
        title="Leaderboard Service API",
        docs_url="/",
        lifespan=lifespan,
    )

    application.add_exception_handler(
        RequestValidationError, validation_exception_handler
    )  # ty: ignore[invalid-argument-type]

    application.include_router(
        leaderboard_router, tags=["leaderboard"], prefix="/api/v1"
    )
    application.include_router(
        leaderboard_dropdowns_router, tags=["leaderboard"], prefix="/api/v1"
    )
    application.include_router(
        leaderboard_stream_router, tags=["leaderboard"], prefix="/api/v1"
    )
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
