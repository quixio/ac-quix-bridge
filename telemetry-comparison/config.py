"""Central config: env vars, paths, rendering constants.

Loaded by every sibling module; tests that need to simulate missing env vars
should `monkeypatch.setattr(config, "QUIXLAKE_URL", None)` etc. — consumers
read attributes off this module rather than capturing values at import time.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
CHANNELS_FILE = BASE_DIR / "channels.json"
DEFAULT_TRACK_CSV = "tracks/ks_nurburgring/layout_sprint_a.csv"

TABLE_NAME = os.getenv("TABLE_NAME", "ac_telemetry")
QUIXLAKE_URL = os.getenv("QUIXLAKE_URL")
QUIX_LAKE_TOKEN = os.getenv("QUIX_LAKE_TOKEN")
BLOB_VIDEO_PREFIX = os.getenv("BLOB_VIDEO_PREFIX", "ac_video")

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
