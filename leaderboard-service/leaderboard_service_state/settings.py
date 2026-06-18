"""Runtime configuration for the State-native leaderboard pipeline.

All values come from environment variables (Quix Cloud injects deployment
variables as env vars). The Lakehouse Query URL / token are auto-injected by Quix
Cloud when the deployment has ``blobStorage: bind: true`` in ``quix.yaml``; we
accept either the canonical ``Quix__Lakehouse__Query__*`` names or the legacy
project-variable names, mirroring ``leaderboard-service/api/settings.py`` and the
best-laps cache.

Identifier-typed settings (``lake_table`` + column names) are validated against
``[A-Za-z_][A-Za-z0-9_]*`` at load time so they are safe to inline into the seed
SQL (the Lakehouse ``/query`` endpoint takes a raw SQL string — no binding).

This is a standalone settings object for the State pipeline so it can run on the
``app.run()`` main thread without importing the FastAPI pydantic ``Settings``
(which requires Mongo / workspace env vars at construction). The HTTP layer keeps
using ``api/settings.py``; the two read overlapping env vars consistently.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SettingsError(RuntimeError):
    """Raised when a required setting is missing or malformed."""


def _validate_identifier(name: str, value: str) -> str:
    if not _IDENTIFIER_RE.match(value):
        raise SettingsError(
            f"{name}={value!r} is not a valid SQL identifier "
            f"(must match [A-Za-z_][A-Za-z0-9_]*)"
        )
    return value


def _first_env(*names: str) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the State-pipeline configuration."""

    # Kafka / Quix
    sdk_token: str | None
    broker_address: str | None
    consumer_group: str
    raw_topic: str
    session_topic: str
    config_topic: str
    events_topic: str

    # DCM
    config_api_url: str | None
    dcm_timeout_s: float

    # Lakehouse Query API
    lakehouse_query_url: str | None
    lakehouse_query_token: str | None

    # Lake schema
    lake_table: str
    col_best_time: str
    col_current_time: str
    col_normalized_position: str

    # Gate algorithm
    gate_count: int

    # State
    state_dir: str

    # Per-lap accumulator safety cap (samples), guards against a stuck stream.
    max_lap_samples: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (and cache) the State-pipeline settings snapshot from the env."""
    lake_table = _validate_identifier(
        "LAKE_TABLE", os.environ.get("LAKE_TABLE", "ac_telemetry")
    )
    col_best_time = _validate_identifier(
        "LAKE_COL_BEST_TIME", os.environ.get("LAKE_COL_BEST_TIME", "iBestTime")
    )
    col_current_time = _validate_identifier(
        "LAKE_COL_CURRENT_TIME",
        os.environ.get("LAKE_COL_CURRENT_TIME", "iCurrentTime"),
    )
    col_normalized_position = _validate_identifier(
        "LAKE_COL_NORMALIZED_POSITION",
        os.environ.get("LAKE_COL_NORMALIZED_POSITION", "normalizedCarPosition"),
    )

    return Settings(
        sdk_token=os.environ.get("Quix__Sdk__Token"),
        broker_address=os.environ.get("BROKER_ADDRESS") or None,
        consumer_group=os.environ.get(
            "STATE_CONSUMER_GROUP", "leaderboard-service-state"
        ),
        raw_topic=os.environ.get("output", "ac-telemetry-raw"),
        session_topic=os.environ.get("session_output", "ac-telemetry-session"),
        config_topic=os.environ.get("config_input", "ac-telemetry-config"),
        events_topic=os.environ.get("LEADERBOARD_EVENTS_TOPIC", "leaderboard-events"),
        config_api_url=os.environ.get("CONFIG_API_URL")
        or "http://dynamic-configuration-manager",
        dcm_timeout_s=float(os.environ.get("DCM_TIMEOUT_S", "5.0")),
        lakehouse_query_url=_first_env(
            "Quix__Lakehouse__Query__Url", "LAKE_API_URL", "QUIXLAKE_URL"
        ),
        lakehouse_query_token=_first_env(
            "Quix__Lakehouse__Query__AuthToken",
            "LAKE_API_TOKEN",
            "QUIX_LAKE_TOKEN",
            "quix_lake_pat",
        ),
        lake_table=lake_table,
        col_best_time=col_best_time,
        col_current_time=col_current_time,
        col_normalized_position=col_normalized_position,
        gate_count=int(os.environ.get("GATE_COUNT", "650")),
        state_dir=os.environ.get("Quix__State__Dir", "state"),
        max_lap_samples=int(os.environ.get("MAX_LAP_SAMPLES", "20000")),
    )
