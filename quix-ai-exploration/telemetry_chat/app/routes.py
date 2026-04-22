"""HTTP routes. Assembled in app/__init__.py via include_router."""

from __future__ import annotations

from fastapi import APIRouter

from .plot import router as plot_router

router = APIRouter()
router.include_router(plot_router)


@router.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
