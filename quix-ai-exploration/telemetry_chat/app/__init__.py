import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import lake, partitions
from .config import LOG_LEVEL, STATIC_DIR
from .routes import router

# Root logger config. Runs once at module import. Setting LOG_LEVEL=DEBUG
# in .env exposes per-SSE-event logs from quix_ai.py so you can watch the
# agent's stream of thought in real time.
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
# httpx prints every outbound request at INFO; one /api/plot call emits
# dozens of lines via the partition walker. Mute unless explicitly needed.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Close module-level httpx clients on shutdown so they don't leak
    connections into the next process lifecycle (matters under pytest +
    uvicorn reload)."""
    try:
        yield
    finally:
        await lake._lake_http.aclose()
        await partitions._http_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Telemetry Chat",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.include_router(router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    return app


app = create_app()
