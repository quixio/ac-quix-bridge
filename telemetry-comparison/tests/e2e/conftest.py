"""E2E test fixtures.

These tests drive a real browser (Chromium via Playwright) against the
running dev server. The server is expected to be up on BASE_URL — start it
yourself before running:

    # terminal 1
    cd telemetry-comparison && uv run fastapi dev main.py --port 8765

    # terminal 2
    cd telemetry-comparison && uv run pytest -m e2e

The dev server hits the real QuixLake configured in .env, so these tests
are slower and flakier than the unit tests. Run them before shipping UI
changes, not on every save.
"""

from __future__ import annotations

import os

import httpx
import pytest

BASE_URL = os.environ.get("TELEMETRY_COMPARISON_URL", "http://localhost:8765")


@pytest.fixture(scope="session", autouse=True)
def _require_server_up() -> None:
    """Fail loudly with a clear message if the dev server isn't running."""
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=3.0)
        r.raise_for_status()
    except Exception as e:
        pytest.exit(
            f"\n\nE2E tests require the dev server to be running at {BASE_URL}."
            f"\nStart it with:  uv run fastapi dev main.py --port 8765"
            f"\nUnderlying error: {e}\n",
            returncode=2,
        )


@pytest.fixture(scope="session")
def base_url() -> str:
    # Session scope to match pytest-base-url's contract (installed as a dep
    # of pytest-playwright).
    return BASE_URL
