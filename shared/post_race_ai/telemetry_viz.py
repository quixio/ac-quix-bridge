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

from .lake import lake_query

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


# Quix palette
_LAP_COLORS = ["#0064ff", "#ff7828", "#00b3a4", "#9b51e0", "#e0218a", "#434352"]
_VALID_BAR = "#0064ff"
_INVALID_BAR = "#b8bdc9"
_FASTEST_BAR = "#ff7828"


def render_telemetry_svg(series: LapSeries) -> str | None:
    """Render the combined telemetry figure to one SVG string, or None if empty."""
    if not series.laps:
        return None

    import io

    import matplotlib

    if matplotlib.get_backend().lower() != "agg":
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.gridspec import GridSpec

    fast_idx = series.fastest_valid_idx
    rows: list[tuple[str, int]] = [("speed", 3)]
    if fast_idx is not None:
        rows += [("pedals", 2), ("gear", 1)]
    rows.append(("laptimes", 2))

    fig = plt.figure(figsize=(7.0, 9.5))
    gs = GridSpec(len(rows), 1, height_ratios=[h for _, h in rows], hspace=0.5)
    axes = {key: fig.add_subplot(gs[i]) for i, (key, _) in enumerate(rows)}

    # 1. Speed — all laps
    ax = axes["speed"]
    for i, lp in enumerate(series.laps):
        ax.plot(
            lp.pos,
            lp.speed,
            color=_LAP_COLORS[i % len(_LAP_COLORS)],
            linewidth=1.0,
            label=f"L{lp.lap} · {format_lap_ms(lp.lap_ms)}",
        )
    ax.set_title("Speed — all laps", fontsize=9, loc="left")
    ax.set_ylabel("km/h", fontsize=8)
    ax.set_xlim(0, 1)
    ax.legend(fontsize=6, loc="lower center", ncol=min(len(series.laps), 4))
    if fast_idx is None:
        ax.set_xlabel("Track position", fontsize=8)

    if fast_idx is not None:
        fast = series.laps[fast_idx]
        ax = axes["pedals"]
        ax.plot(fast.pos, fast.gas, color="#00b3a4", linewidth=1.0, label="Throttle")
        ax.plot(fast.pos, fast.brake, color="#d12d2d", linewidth=1.0, label="Brake")
        ax.set_title(
            f"Throttle & brake — fastest lap (L{fast.lap}, {format_lap_ms(fast.lap_ms)})",
            fontsize=9,
            loc="left",
        )
        ax.set_ylabel("0–1", fontsize=8)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.05)
        ax.legend(fontsize=6, loc="upper right")

        ax = axes["gear"]
        ax.step(fast.pos, fast.gear, color="#0a0b24", linewidth=1.0, where="post")
        ax.set_title("Gear — fastest lap", fontsize=9, loc="left")
        ax.set_ylabel("gear", fontsize=8)
        ax.set_xlim(0, 1)
        ax.set_xlabel("Track position", fontsize=8)

    # 4. Lap times
    ax = axes["laptimes"]
    xs = list(range(len(series.laps)))
    colors = [
        _FASTEST_BAR
        if i == series.fastest_valid_idx
        else (_VALID_BAR if lp.valid else _INVALID_BAR)
        for i, lp in enumerate(series.laps)
    ]
    ax.bar(xs, [lp.lap_ms / 1000 for lp in series.laps], color=colors)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"L{lp.lap}" for lp in series.laps], fontsize=7)
    ax.set_title("Lap times (orange=fastest, grey=invalid)", fontsize=9, loc="left")
    ax.set_ylabel("seconds", fontsize=8)

    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def resolve_lake_keys(analysis: "Analysis", test: "Test") -> tuple[str, str, str] | None:
    """Resolve (driver_lowercased, track, car_model) for the lake query, or None.

    Session-level only. driver from context/extra (lowercased to match the lake
    partition); track + car_model prefer the matching SessionInfo (authoritative).
    """
    if not analysis.session_id:
        return None
    ctx = analysis.context
    extra = analysis.extra or {}
    driver = (ctx.driver if ctx else None) or extra.get("driver")
    track = (ctx.track if ctx else None) or extra.get("track")
    car = (ctx.car_model if ctx else None) or extra.get("car_model")
    for s in test.sessions:
        if s.session_id == analysis.session_id:
            track = s.track or track
            car = s.car_model or car
            break
    if not (driver and track and car):
        logger.info(
            "[viz] resolve_lake_keys: incomplete (driver=%s track=%s car=%s)",
            driver,
            track,
            car,
        )
        return None
    return driver.lower(), track, car


def build_analysis_telemetry_svg(
    analysis: "Analysis", test: "Test", table: str
) -> str | None:
    """Best-effort: resolve keys, query the lake, clean, render. Never raises."""
    try:
        keys = resolve_lake_keys(analysis, test)
        if keys is None:
            return None
        driver, track, car = keys
        assert analysis.session_id is not None
        sql = build_session_sql(table, analysis.session_id, driver, track, car)
        df = lake_query(sql)
        series = clean_laps(df)
        svg = render_telemetry_svg(series)
        if svg is None:
            logger.info("[viz] no telemetry plots for analysis %s", analysis.id)
        return svg
    except Exception:
        logger.warning(
            "[viz] telemetry build failed for analysis %s",
            getattr(analysis, "id", "?"),
            exc_info=True,
        )
        return None
