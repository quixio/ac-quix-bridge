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


def _trim_lap1_staging(g: pd.DataFrame) -> pd.DataFrame:
    """Drop the hotlap staging prefix of lap 1 (Telemetry Explorer wrap logic)."""
    g = g.sort_values("timestamp_ms").reset_index(drop=True)
    pos = g["pos"].to_numpy()
    for i in range(1, len(pos)):
        if pos[i - 1] > 0.9 and pos[i] < 0.1:
            return g.iloc[i:]
    if len(g) and g["pos"].min() > 0.1:
        return g.iloc[0:0]  # out-lap only, no full circuit
    return g


def _downsample(g: pd.DataFrame, n_bins: int) -> pd.DataFrame:
    """Bin pos into n_bins equal bins (0..1), mean per bin, drop empty bins."""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bins = pd.cut(g["pos"], bins=edges, include_lowest=True)
    cols = ["pos", "speed", "gas", "brake", "gear"]
    agg = g.groupby(bins, observed=True)[cols].mean().dropna(subset=["pos"])
    return agg.reset_index(drop=True).sort_values("pos").reset_index(drop=True)


def clean_laps(
    df: pd.DataFrame,
    *,
    min_samples: int = 1000,
    n_bins: int = 400,
    invalid_tol: int = 5,
) -> LapSeries:
    """Turn a raw per-session telemetry DataFrame into plot-ready per-lap series."""
    if df is None or df.empty:
        logger.info("[viz] clean_laps: empty input")
        return LapSeries()
    df = df.rename(columns={"speedKmh": "speed"})
    n_raw = len(df)
    df = df[
        df["speed"].between(0, 400)
        & df["gas"].between(0, 1)
        & df["brake"].between(0, 1)
        & df["gear"].between(0, 8)
    ]
    if len(df) < n_raw:
        logger.info("[viz] clean_laps: dropped %d out-of-range rows", n_raw - len(df))
    if df.empty:
        return LapSeries()

    all_laps = sorted(int(x) for x in df["lap"].unique())
    max_lap = max(all_laps)
    logger.info("[viz] clean_laps: laps=%s, dropping last lap %d", all_laps, max_lap)

    out: list[Lap] = []
    for lap in all_laps:
        if lap == max_lap:
            continue
        g = df[df["lap"] == lap]
        if len(g) <= min_samples:
            logger.info("[viz] clean_laps: drop sliver lap %d (n=%d)", lap, len(g))
            continue
        lap_ms = int(g["iCurrentTime"].max())
        n_invalid = int((g["isValidLap"] == 0).sum())
        valid = n_invalid <= invalid_tol
        if lap == 1:
            g = _trim_lap1_staging(g)
            if g.empty:
                logger.info("[viz] clean_laps: lap 1 out-lap only, dropped")
                continue
        binned = _downsample(g, n_bins)
        if binned.empty:
            logger.info("[viz] clean_laps: lap %d empty after downsample", lap)
            continue
        out.append(
            Lap(
                lap=lap,
                pos=binned["pos"].tolist(),
                speed=binned["speed"].tolist(),
                gas=binned["gas"].tolist(),
                brake=binned["brake"].tolist(),
                gear=binned["gear"].tolist(),
                lap_ms=lap_ms,
                valid=valid,
            )
        )

    valid_idx = [i for i, lp in enumerate(out) if lp.valid]
    fastest = min(valid_idx, key=lambda i: out[i].lap_ms) if valid_idx else None
    logger.info(
        "[viz] clean_laps: kept %d laps, fastest_valid_idx=%s", len(out), fastest
    )
    return LapSeries(laps=out, fastest_valid_idx=fastest)
