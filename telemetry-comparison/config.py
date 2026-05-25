"""Central config: env vars, paths, rendering constants.

Loaded by every sibling module; tests that need to simulate missing env vars
should `monkeypatch.setattr(config, "QUIXLAKE_URL", None)` etc. — consumers
read attributes off this module rather than capturing values at import time.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
CHANNELS_FILE = BASE_DIR / "channels.json"
DEFAULT_TRACK_CSV = "tracks/ks_nurburgring/layout_sprint_a.csv"

TABLE_NAME = os.getenv("TABLE_NAME", "ac_telemetry")
QUIXLAKE_URL = os.getenv("QUIXLAKE_URL")
QUIX_LAKE_TOKEN = os.getenv("QUIX_LAKE_TOKEN")
BLOB_VIDEO_PREFIX = os.getenv("BLOB_VIDEO_PREFIX", "ac_video")

# Quix Portal API base. `Quix__Portal__Api` is the canonical name — Quix Cloud
# auto-injects it on every deployment, and we use the same name in local .env.
PORTAL = os.getenv("Quix__Portal__Api", "").rstrip("/")  # noqa: SIM112
QUIX_TOKEN = os.getenv("QUIX_TOKEN", "")

# Bearer-token auth gate. Tokens are validated against Quix Portal via the
# `quixportal` SDK, scoped to this workspace.
WORKSPACE_ID = os.getenv("Quix__Workspace__Id", "")  # noqa: SIM112
API_AUTH_ACTIVE = os.getenv("API_AUTH_ACTIVE", "true").lower() == "true"
LOCAL_DEV_MODE = os.getenv("LOCAL_DEV_MODE", "").lower() == "true"

# QuixLake Querier agent (system prompt + KBs + MCP tools live on it).
# Override via env if you need to point at a fork of the agent.
_DEFAULT_AGENT_ID = "d578e2f5-c2b7-461a-90d2-70dfac450fb0"
AGENT_CONFIGURATION_ID = os.getenv("QUIX_AI_AGENT_ID", _DEFAULT_AGENT_ID)


def portal_headers(*, streaming: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {QUIX_TOKEN}",
        "Content-Type": "application/json",
    }
    if streaming:
        headers["Accept"] = "text/event-stream"
    return headers


def validate_env() -> None:
    """Log missing/default env vars at startup so misconfig is visible up
    front. ERRORs for hard requirements; WARNs for soft (default-backed)
    settings. Does not raise — request-time guards (in main.py / chat.py)
    still surface clean errors to the caller.
    """
    required = {
        "QUIXLAKE_URL": QUIXLAKE_URL,
        "QUIX_LAKE_TOKEN": QUIX_LAKE_TOKEN,
        "Quix__Portal__Api": PORTAL,
        "QUIX_TOKEN": QUIX_TOKEN,
    }
    for name, value in required.items():
        if not value:
            logger.error("Required env var %s is not set", name)

    if API_AUTH_ACTIVE and not LOCAL_DEV_MODE and not WORKSPACE_ID:
        logger.error("Quix__Workspace__Id is not set — Bearer-token auth will reject every request")
    if not API_AUTH_ACTIVE:
        logger.warning("API_AUTH_ACTIVE=false — all requests bypass authentication")
    if LOCAL_DEV_MODE:
        logger.warning("LOCAL_DEV_MODE=true — using mock auth (all permissions granted)")

    if AGENT_CONFIGURATION_ID == _DEFAULT_AGENT_ID and not os.getenv("QUIX_AI_AGENT_ID"):
        logger.warning(
            "QUIX_AI_AGENT_ID not set — using default QuixLake Querier agent %s",
            _DEFAULT_AGENT_ID,
        )


CORNER_THRESHOLDS = {"hairpin_max": 60, "tight_max": 150, "sweeper_max": 400}
TRACK_COLORS = {
    "hairpin": "#f87171",
    "tight": "#fb923c",
    "sweeper": "#fbbf24",
    "straight": "#34d399",
    "start_finish": "#ffffff",
    "marker": "#fff8e1",
    "track_dot": "#ef4444",
}
CORNER_MIN_LENGTH_M = 20
