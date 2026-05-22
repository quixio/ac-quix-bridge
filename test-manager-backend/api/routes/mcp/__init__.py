"""MCP server mounted at /mcp on test-manager-backend.

Auth: `X-API-Key` header against `Settings.testmanager_mcp_api_key`.
Tool registration: FastMCP with name slug "test-manager"; Quix.AI bridges
tool names to Claude as `mcp__test-manager__<tool>`.
"""

import logging
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from pymongo.database import Database
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...auth import _token_preview
from ...settings import get_settings
from .handlers.core import get_session, get_test, list_logbook
from .handlers.history import (
    list_recent_sessions_for_driver,
    list_sessions_for_test,
)
from .handlers.lookups import get_device, get_driver, get_environment
from .handlers.write import save_analysis
from .instrument import instrument_tool
from .tools import TOOL_TITLES

logger = logging.getLogger(__name__)


def _build_tools(mongo: Database[dict[str, Any]]) -> dict[str, Any]:
    """Returns mapping of MCP tool name → callable. Mongo is bound at registration time."""

    def _get_test(*, test_id: str) -> dict[str, Any]:
        return get_test(mongo, test_id=test_id)

    def _get_session(*, test_id: str, session_id: str) -> dict[str, Any]:
        return get_session(mongo, test_id=test_id, session_id=session_id)

    def _list_logbook(
        *,
        test_id: str,
        session_id: str | None = None,
        include_test_wide: bool = False,
    ) -> list[dict[str, Any]]:
        return list_logbook(
            mongo,
            test_id=test_id,
            session_id=session_id,
            include_test_wide=include_test_wide,
        )

    def _get_driver(*, id: str) -> dict[str, Any]:
        return get_driver(mongo, id=id)

    def _get_device(*, id: str) -> dict[str, Any]:
        return get_device(mongo, id=id)

    def _get_environment(*, id: str) -> dict[str, Any]:
        return get_environment(mongo, id=id)

    def _list_sessions_for_test(*, test_id: str) -> list[dict[str, Any]]:
        return list_sessions_for_test(mongo, test_id=test_id)

    def _list_recent_sessions_for_driver(
        *, driver_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        return list_recent_sessions_for_driver(mongo, driver_id=driver_id, limit=limit)

    def _save_analysis(
        *,
        analysis_id: str,
        summary_md: str,
        kpis: list[dict[str, Any]] | None = None,
        requirements_check: list[dict[str, Any]] | None = None,
        logbook_refs: list[str] | None = None,
        anomalies: list[dict[str, Any]] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return save_analysis(
            mongo,
            analysis_id=analysis_id,
            summary_md=summary_md,
            kpis=kpis,
            requirements_check=requirements_check,
            logbook_refs=logbook_refs,
            anomalies=anomalies,
            extra=extra,
        )

    return {
        "get_test": _get_test,
        "get_session": _get_session,
        "list_logbook": _list_logbook,
        "get_driver": _get_driver,
        "get_device": _get_device,
        "get_environment": _get_environment,
        "list_sessions_for_test": _list_sessions_for_test,
        "list_recent_sessions_for_driver": _list_recent_sessions_for_driver,
        "save_analysis": _save_analysis,
    }


class _ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject /mcp requests lacking the configured X-API-Key.

    Reads the expected key from settings per-request so that env / config
    changes after app construction (including test fixtures) take effect.
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        expected_key = get_settings().testmanager_mcp_api_key
        provided = request.headers.get("X-API-Key", "")
        if not expected_key or provided != expected_key:
            origin = request.client.host if request.client else "unknown"
            logger.warning(
                "[mcp] wrong X-API-Key from %s (provided=%s)",
                origin,
                _token_preview(provided),
            )
            return JSONResponse({"detail": "invalid api key"}, status_code=401)
        return await call_next(request)


def install(app: FastAPI, mongo: Database[dict[str, Any]]) -> None:
    """Mount the MCP server at /mcp with API-key auth middleware."""
    mcp = FastMCP(name="test-manager")

    tools = _build_tools(mongo)
    missing_titles = set(tools) - set(TOOL_TITLES)
    if missing_titles:
        raise RuntimeError(f"MCP tools missing TOOL_TITLES entries: {missing_titles}")
    for name, fn in tools.items():
        title = TOOL_TITLES.get(name)
        mcp.tool(name=name, title=title)(instrument_tool(name, fn, logger))

    sub_app = mcp.streamable_http_app()
    sub_app.add_middleware(_ApiKeyMiddleware)

    app.mount("/mcp", sub_app)
    logger.info("[mcp] mounted at /mcp (tools=%d)", len(tools))
