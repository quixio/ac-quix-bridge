"""MCP server mounted into the Telemetry Explorer FastAPI app at `/mcp`.

Exposes one tool, `plot_data`, that the QuixLake Querier agent calls to draw a
chart for the user. The tool itself is a thin render-directive: it validates the
plot spec and returns a confirmation. The actual chart is drawn in the browser —
the frontend sees the `plot_data` tool call (name + args) on the AI SSE stream
and feeds the args to `applyPlotPlan`. This replaces the brittle ```json fence
the agent used to embed in prose.

Mirrors test-manager-backend's mount-into-FastAPI pattern: `install(app)` builds
the FastMCP server, mounts its streamable-HTTP sub-app under `/mcp`, gates it
with an `X-API-Key` middleware, and returns the server so `main.py`'s lifespan
can drive `session_manager.run()` (required for mcp>=1.27).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import config
from plans import PlotPlan, Trace

logger = logging.getLogger(__name__)


def plot_data(*, signals: list[str], traces: list[Trace], title: str = "") -> dict[str, Any]:
    """Draw a telemetry chart in the Telemetry Explorer for the user.

    Call this to PLOT data instead of describing it in prose. The chart is
    rendered in the user's browser; this returns only a confirmation.

    Args:
        signals: Channel names to plot, e.g. ["speedKmh", "gas", "brake"]. At
            least one.
        traces: One entry per (session, lap) to overlay. Each must carry the
            full partition path: session_id, lap, driver, carModel, track,
            experiment, environment, test_rig. At least one.
        title: Optional chart title.
    """
    plan = PlotPlan(type="plot", title=title, signals=signals, traces=traces)
    logger.info("plot_data: %d trace(s), signals=%s", len(plan.traces), plan.signals)
    return {
        "status": "plotted",
        "trace_count": len(plan.traces),
        "signals": plan.signals,
    }


class _ApiKeyMiddleware(BaseHTTPMiddleware):
    """Require a valid `X-API-Key` on every request to the mounted MCP app.

    Fail-closed: 500 if the key env var is unset, 401 on missing/mismatch.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        expected = config.MCP_API_KEY
        if not expected:
            logger.error("MCP_API_KEY (TELEMETRY_COMPARISON_MCP_API_KEY) is not set")
            return JSONResponse({"detail": "mcp not configured"}, status_code=500)
        provided = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(provided, expected):
            return JSONResponse({"detail": "invalid api key"}, status_code=401)
        return await call_next(request)


def install(app: FastAPI) -> FastMCP:
    """Build a fresh MCP server, (re)mount it at `/mcp`, and return it.

    The caller MUST run `mcp.session_manager.run()` in the app lifespan, else
    tool calls fail with "Task group is not initialized".

    A new FastMCP is created per call because its session manager can only be
    run() once per instance, and the app's lifespan re-runs on every startup
    (e.g. each TestClient context). We drop any prior `/mcp` mount first so the
    mounted sub-app always belongs to the current, runnable session manager.
    """
    mcp = FastMCP(
        name="telemetry-comparison",
        host="0.0.0.0",  # disable FastMCP DNS-rebind guard (else 421 externally)
        streamable_http_path="/",
    )
    mcp.tool(name="plot_data", title="Plot data")(plot_data)
    sub_app = mcp.streamable_http_app()
    sub_app.add_middleware(_ApiKeyMiddleware)
    app.routes[:] = [r for r in app.routes if getattr(r, "path", None) != "/mcp"]
    app.mount("/mcp", sub_app)
    logger.info("MCP mounted at /mcp (api key configured: %s)", bool(config.MCP_API_KEY))
    return mcp
