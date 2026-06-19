"""Runtime configuration for the best-laps cache service.

All values come from environment variables (Quix Cloud injects deployment
variables as env vars; the SDK token + portal API are injected when
``Quix__Sdk__Token`` is set). The Lakehouse Query URL / token are
auto-injected by Quix Cloud when the deployment has ``blobStorage: bind:
true`` in ``quix.yaml`` — we accept either the canonical
``Quix__Lakehouse__Query__*`` names or the legacy project-variable names so
the service works regardless of how the portal mapped them, mirroring
``leaderboard-service/api/settings.py``.

Identifier-typed settings (``lake_table``, ``col_best_time``) are validated
against ``[A-Za-z_][A-Za-z0-9_]*`` at load time so they are safe to inline
directly into the reconcile SQL (the Lakehouse ``/query`` endpoint takes a
raw SQL string — no parameter binding).
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
    """Return *value* if it is a safe SQL identifier, else raise.

    Guards the table / column names that get inlined into the reconcile
    SQL string against injection (they come from deployment env vars, not
    user input, but validation is cheap insurance).
    """
    if not _IDENTIFIER_RE.match(value):
        raise SettingsError(
            f"{name}={value!r} is not a valid SQL identifier "
            f"(must match [A-Za-z_][A-Za-z0-9_]*)"
        )
    return value


def _first_env(*names: str) -> str | None:
    """Return the first non-empty env var among *names*, else ``None``."""
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the service configuration."""

    # Kafka / Quix
    sdk_token: str | None
    broker_address: str | None
    consumer_group: str
    raw_topic: str
    session_topic: str
    config_topic: str

    # DCM
    config_api_url: str | None
    dcm_timeout_s: float

    # Lakehouse Query API
    lakehouse_query_url: str | None
    lakehouse_query_token: str | None

    # Lake schema
    lake_table: str
    col_best_time: str

    # HTTP
    http_host: str
    http_port: int

    # State
    state_dir: str

    # Boot seed
    boot_seed_gate_timeout_s: float


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Build (and cache) the settings snapshot from the environment."""
    lake_table = _validate_identifier(
        "LAKE_TABLE", os.environ.get("LAKE_TABLE", "ac_telemetry_prod")
    )
    col_best_time = _validate_identifier(
        "LAKE_COL_BEST_TIME", os.environ.get("LAKE_COL_BEST_TIME", "iBestTime")
    )

    return Settings(
        sdk_token=os.environ.get("Quix__Sdk__Token"),
        broker_address=os.environ.get("BROKER_ADDRESS") or None,
        consumer_group=os.environ.get("CONSUMER_GROUP", "best-laps-cache"),
        raw_topic=os.environ.get("output", "ac-telemetry-raw"),
        session_topic=os.environ.get("session_output", "ac-telemetry-session"),
        config_topic=os.environ.get("config_input", "ac-telemetry-config"),
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
        http_host=os.environ.get("HTTP_HOST", "0.0.0.0"),
        http_port=int(os.environ.get("HTTP_PORT", "80")),
        state_dir=os.environ.get("Quix__State__Dir", "state"),
        boot_seed_gate_timeout_s=float(
            os.environ.get("BOOT_SEED_GATE_TIMEOUT_S", "60.0")
        ),
    )
