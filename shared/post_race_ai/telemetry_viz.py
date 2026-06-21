"""Post-race telemetry visualization: lake fetch → clean → one combined SVG.

Best-effort throughout; the orchestrator never raises. Cleaning rules and the
hotlap lap-1 staging trim are documented in the design spec
(docs/superpowers/specs/2026-06-21-post-race-telemetry-viz-design.md).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from api.models import Analysis, Test

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Lap:
    lap: int
    pos: list[float]
    speed: list[float]
    gas: list[float]
    brake: list[float]
    gear: list[float]
    lap_ms: int
    valid: bool


@dataclass
class LapSeries:
    laps: list[Lap] = field(default_factory=list)
    fastest_valid_idx: int | None = None


def format_lap_ms(ms: int) -> str:
    """Format a lap time in ms as m:ss.mmm (e.g. 144795 -> '2:24.795')."""
    minutes = ms // 60000
    seconds = (ms % 60000) / 1000
    return f"{minutes}:{seconds:06.3f}"


def build_session_sql(
    table: str, session_id: str, driver: str, track: str, car_model: str
) -> str:
    """Build the single per-session telemetry query (partition-equality, prunable)."""
    if not _IDENT_RE.match(table):
        raise ValueError(f"unsafe table identifier: {table!r}")

    def q(v: str) -> str:
        return v.replace("'", "''")

    return (
        "SELECT lap, normalizedCarPosition AS pos, speedKmh, gas, brake, gear, "
        "iCurrentTime, isValidLap, timestamp_ms "
        f"FROM {table} "
        f"WHERE session_id = '{q(session_id)}' AND driver = '{q(driver)}' "
        f"AND track = '{q(track)}' AND carModel = '{q(car_model)}'"
    )
