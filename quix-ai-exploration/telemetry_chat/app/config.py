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

# Shared password gating every route. Empty = all requests 401 (fail closed).
# Set as a Quix Secret variable in cloud deployments; share via password manager.
SHARED_PASSWORD = os.environ.get("SHARED_PASSWORD", "")

QUIXLAKE_URL = os.environ.get("QUIXLAKE_URL", "")
QUIX_LAKE_TOKEN = os.environ.get("QUIX_LAKE_TOKEN", "")
TABLE_NAME = os.environ.get("TABLE_NAME", "ac_telemetry")

# QuixLake Querier agent (carries the system prompt + KBs + MCP tools).
# Override via env if you need to point at a fork of the agent.
AGENT_CONFIGURATION_ID = os.environ.get(
    "QUIX_AI_AGENT_ID", "d578e2f5-c2b7-461a-90d2-70dfac450fb0"
)

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
