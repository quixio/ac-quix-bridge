"""Env + paths. Loaded once; consumers read module attrs at call time so tests
can monkeypatch them without re-importing."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
CHANNELS_FILE = STATIC_DIR / "channels.json"

load_dotenv(ROOT / ".env")

PORTAL = os.environ.get("QUIX_PORTAL_API", "").rstrip("/")
QUIX_TOKEN = os.environ.get("QUIX_TOKEN", "")

QUIXLAKE_URL = os.environ.get("QUIXLAKE_URL", "")
QUIX_LAKE_TOKEN = os.environ.get("QUIX_LAKE_TOKEN", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "ac_telemetry")

SESSIONS_CACHE_TTL = float(os.environ.get("SESSIONS_CACHE_TTL", "60"))

# Set LOG_LEVEL=DEBUG in .env to see each Quix AI SSE event as it arrives.
# Default INFO — app lifecycle + errors only, no per-event noise.
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()


def portal_headers(*, streaming: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {QUIX_TOKEN}",
        "Content-Type": "application/json",
    }
    if streaming:
        headers["Accept"] = "text/event-stream"
    return headers


def lake_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {QUIX_LAKE_TOKEN}",
        "Content-Type": "text/plain",
    }
