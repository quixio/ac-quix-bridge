"""Post-race telemetry visualization: lake fetch → clean → one combined SVG.

Best-effort throughout; the orchestrator never raises. Cleaning rules and the
hotlap lap-1 staging trim are documented in the design spec
(docs/superpowers/specs/2026-06-21-post-race-telemetry-viz-design.md).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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


# matplotlib "tab10" — 10 distinct categorical colours, 1:1 with the speed legend.
_LAP_COLORS = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]
# Quix bar palette for the lap-time chart
_VALID_BAR = "#0064ff"
_FASTEST_BAR = "#ff7828"
_THROTTLE = "#0064ff"  # Quix blue
_BRAKE = "#ff7828"  # Quix orange
_GEAR = "#9b51e0"  # Quix violet (logo)
_GRID = "#e3e3f2"


def _declutter(ax: Any) -> None:
    """Drop the top/right spines and add a light horizontal grid (clean look)."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", color=_GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=7)


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
    from matplotlib.patches import Patch

    # Thicker hatch lines so the diagonal reads in the small legend swatch
    # (hatch line width is global — there's no per-artist override).
    matplotlib.rcParams["hatch.linewidth"] = 1.8

    lap_colors = _LAP_COLORS
    legend_kw = {
        "fontsize": 6,
        "loc": "upper left",
        "bbox_to_anchor": (1.01, 1.0),
        "borderaxespad": 0.0,
        "frameon": False,
    }
    legends = []

    # Prefer the fastest valid lap for the pedal/gear detail; if none is valid
    # (e.g. every lap cut track limits) fall back to the fastest lap overall so
    # the throttle/brake/gear traces still render — labelled invalid.
    fast_idx = series.fastest_valid_idx
    if fast_idx is None and series.laps:
        fast_idx = min(range(len(series.laps)), key=lambda i: series.laps[i].lap_ms)
    fast_is_valid = fast_idx is not None and series.laps[fast_idx].valid
    rows: list[tuple[str, int]] = [("speed", 6)]
    if fast_idx is not None:
        rows += [("pedals", 5), ("gear", 5)]
    rows.append(("laptimes", 5))

    fig = plt.figure(figsize=(7.0, 11.3))
    gs = GridSpec(len(rows), 1, height_ratios=[h for _, h in rows], hspace=0.6)
    axes = {key: fig.add_subplot(gs[i]) for i, (key, _) in enumerate(rows)}
    for ax in axes.values():
        _declutter(ax)

    # 1. Speed — all laps, legend (lap · time) below the plot so the axes span
    # the full width and the legend never covers the trace (scales with laps).
    ax = axes["speed"]
    for i, lp in enumerate(series.laps):
        ax.plot(
            lp.pos,
            lp.speed,
            color=lap_colors[i % len(lap_colors)],
            linewidth=1.0,
            label=f"L{lp.lap} · {format_lap_ms(lp.lap_ms)}",
        )
    ax.set_title("Speed — all laps", fontsize=8, fontweight="bold", loc="center")
    ax.set_ylabel("Speed [km/h]", fontsize=8)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Track Position [-]", fontsize=8)
    legends.append(ax.legend(**legend_kw))

    if fast_idx is not None:
        fast = series.laps[fast_idx]
        lap_kind = "fastest valid lap" if fast_is_valid else "fastest lap"
        lap_label = f"L{fast.lap}, {format_lap_ms(fast.lap_ms)}"
        if not fast_is_valid:
            lap_label += " · invalid"
        # 2. Throttle + brake (filled) — fastest lap.
        ax = axes["pedals"]
        ax.fill_between(fast.pos, 0, fast.gas, color=_THROTTLE, alpha=0.35, linewidth=0)
        ax.plot(fast.pos, fast.gas, color=_THROTTLE, linewidth=0.9, label="Throttle")
        ax.fill_between(fast.pos, 0, fast.brake, color=_BRAKE, alpha=0.30, linewidth=0)
        ax.plot(fast.pos, fast.brake, color=_BRAKE, linewidth=0.9, label="Brake")
        ax.set_title(
            f"Throttle & brake — {lap_kind} ({lap_label})",
            fontsize=8,
            fontweight="bold",
            loc="center",
        )
        ax.set_ylabel("Throttle / Brake [-]", fontsize=8)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.05)
        ax.set_xlabel("Track Position [-]", fontsize=8)
        legends.append(ax.legend(**legend_kw))

        # 3. Gear — real gear (AC raw value is offset by 1: 0=R, 1=N, 2=1st…).
        ax = axes["gear"]
        real_gear = [g - 1 for g in fast.gear]
        ax.step(fast.pos, real_gear, color=_GEAR, linewidth=1.1, where="post")
        ax.set_title(
            f"Gear — {lap_kind} ({lap_label})",
            fontsize=8,
            fontweight="bold",
            loc="center",
        )
        ax.set_ylabel("Gear [-]", fontsize=8)
        ax.set_xlim(0, 1)
        gmax = max(6, int(max(real_gear))) if real_gear else 6
        ax.set_ylim(0.5, gmax + 0.5)
        ax.set_yticks(list(range(1, gmax + 1)))
        ax.set_xlabel("Track Position [-]", fontsize=8)

    # 4. Lap times — absolute time, y-axis zoomed to the lap-time range so the
    # sub-second spread is visible (fastest = shortest orange bar).
    ax = axes["laptimes"]
    xs = list(range(len(series.laps)))
    lap_s = [lp.lap_ms / 1000 for lp in series.laps]
    # Orange = fastest VALID lap only (matches the legend); the pedal/gear
    # fallback to an invalid lap must NOT colour a bar orange.
    colors = [
        _FASTEST_BAR if i == series.fastest_valid_idx else _VALID_BAR
        for i in range(len(series.laps))
    ]
    bars = ax.bar(xs, lap_s, color=colors)
    # Invalid laps: same blue, diagonal white hatch (= "lap exists but excluded").
    for bar, lp in zip(bars, series.laps):
        if not lp.valid:
            bar.set_hatch("//")
            bar.set_edgecolor("white")
            bar.set_linewidth(0.0)
    lo, hi = min(lap_s), max(lap_s)
    pad = max(0.4, (hi - lo) * 0.25)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"L{lp.lap}" for lp in series.laps], fontsize=7)
    ax.set_title("Lap times", fontsize=8, fontweight="bold", loc="center")
    ax.set_ylabel("Lap Time [s]", fontsize=8)
    ax.set_xlabel("Lap [-]", fontsize=8)
    bar_legend = [
        Patch(facecolor=_FASTEST_BAR, label="Fastest valid"),
        Patch(facecolor=_VALID_BAR, label="Valid"),
        Patch(facecolor=_VALID_BAR, hatch="///", edgecolor="white", label="Invalid"),
    ]
    # Bigger uniform handle so the hatch reads and all swatches match in size.
    legends.append(
        ax.legend(handles=bar_legend, handlelength=2.6, handleheight=1.4, **legend_kw)
    )
    for bar, lp in zip(bars, series.laps):
        ax.annotate(
            format_lap_ms(lp.lap_ms),
            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
            ha="center",
            va="bottom",
            fontsize=6,
            xytext=(0, 2),
            textcoords="offset points",
        )

    buf = io.StringIO()
    fig.savefig(buf, format="svg", bbox_inches="tight", bbox_extra_artists=legends)
    plt.close(fig)
    return buf.getvalue()


def resolve_lake_keys(
    analysis: "Analysis", test: "Test"
) -> tuple[str, str, str] | None:
    """Resolve (driver_lowercased, track, car_model) for the lake query, or None.

    Session-level only. Values must equal the lake's partition values EXACTLY, so
    they come only from authoritative sources, never the AI-supplied `extra`:
    - driver: `context.driver` (a stamped copy of `test.driver`) else `test.driver`,
      lowercased — the same rule the lake used (`build_partition_values`:
      `test.driver.lower()`).
    - track / car_model: the matching `SessionInfo` (verbatim AC codes, exactly what
      the lake stored), falling back to the stamped `context`.
    """
    if not analysis.session_id:
        return None
    ctx = analysis.context
    driver = (ctx.driver if ctx else None) or test.driver
    track = ctx.track if ctx else None
    car = ctx.car_model if ctx else None
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
