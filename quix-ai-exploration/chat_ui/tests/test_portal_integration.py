"""Integration tests — hit real Quix Portal. Opt-in via --run-slow.

Skipped by default so `uv run pytest` stays fast. Run with:

    uv run pytest --run-slow -m integration
"""

from __future__ import annotations

import httpx
import pytest

from app.config import PORTAL, portal_headers


@pytest.mark.integration
@pytest.mark.slow
async def test_portal_reachable() -> None:
    """Sanity check: the configured Portal base responds to an authenticated GET.

    Uses a known-safe read endpoint (KB list) so no state is mutated.
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"{PORTAL}/ai/api/knowledge-bases", headers=portal_headers()
        )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
