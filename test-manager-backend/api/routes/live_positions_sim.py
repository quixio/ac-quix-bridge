"""Multi-driver live-positions simulator for the Analysis Leaderboard.

LOCAL_DEV_MODE only. Pure functions + a single module-level dict for the
active driver's running lap state per (track, car, experiment). No I/O,
no Kafka, no DB. Returns a flat list of `LivePositionEntry` dicts ready
for `pydantic`-validation by the route.

Why this shape:

* The product question is "where do I sit in a group of historical
  drivers as I progress through the lap?". The display is a 5-row table
  per (track, car, experiment) group, and the active driver's rank
  *shifts at sector boundaries* based on his cumulative-at-sector time
  versus the historical drivers' cumulative-at-sector times.

* All numbers (best laps, splits, lap-targets) are deterministic from a
  small set of constants so the table is stable across requests; only
  the active driver's running clock advances over time.

* Active driver = "Ludvík" in every group. The historical field is
  `HISTORICAL_DRIVER_COUNT` drivers wide (currently 100) — the first
  four keep their original hand-picked names (Alice/Bob/Carla/Diego)
  and the rest are synthetic "Driver-NNN" entries. Server-computed
  rank avoids any client-side sorting drift.

Public API: `make_local_dev_live_positions()`.
"""

from __future__ import annotations

import time

# ---------------------------------------------------------------------------
# Static matrix
# ---------------------------------------------------------------------------

TRACKS: list[str] = ["ks_nurburgring", "spa", "silverstone"]
CARS: list[str] = ["bmw_1m", "ferrari_488"]
EXPERIMENTS: list[str] = ["baseline", "tuned"]

ACTIVE_DRIVER: str = "Ludvík"

# Size of the historical field per (track, car, experiment) group. Bumped
# from the original 4 to 100 to exercise the frontend with a large
# best-laps table. The active driver is in addition to this count, so each
# group ships HISTORICAL_DRIVER_COUNT + 1 rows.
HISTORICAL_DRIVER_COUNT: int = 100

_NAMED_HISTORICALS: list[str] = ["Alice", "Bob", "Carla", "Diego"]


def _make_historicals() -> list[str]:
    extra = [
        f"Driver-{i:03d}"
        for i in range(len(_NAMED_HISTORICALS) + 1, HISTORICAL_DRIVER_COUNT + 1)
    ]
    return (_NAMED_HISTORICALS + extra)[:HISTORICAL_DRIVER_COUNT]


# Driver order matters: index 0 is the active driver, indices 1..N are the
# static historical drivers. The base formula uses this index.
DRIVERS: list[str] = [ACTIVE_DRIVER] + _make_historicals()


# Per-driver sector splits — fractions of that driver's own best_lap_ms.
# Each row sums to 1.0 (within float epsilon). The active driver's splits
# also define the sector boundaries used for rank evaluation. The five
# originally-named drivers keep their hand-picked splits; synthetic
# drivers get a deterministic spread around (1/3, 1/3, 1/3).
_KNOWN_SPLITS: dict[str, tuple[float, float, float]] = {
    "Ludvík": (0.33, 0.33, 0.34),
    "Alice": (0.31, 0.34, 0.35),
    "Bob": (0.36, 0.32, 0.32),
    "Carla": (0.34, 0.33, 0.33),
    "Diego": (0.33, 0.31, 0.36),
}


def _splits_for(idx: int, driver: str) -> tuple[float, float, float]:
    if driver in _KNOWN_SPLITS:
        return _KNOWN_SPLITS[driver]
    s0 = 0.33 + (((idx * 7) % 11) - 5) * 0.002
    s1 = 0.33 + (((idx * 13) % 9) - 4) * 0.002
    s2 = 1.0 - s0 - s1
    return s0, s1, s2


SPLITS: dict[str, tuple[float, float, float]] = {
    d: _splits_for(i, d) for i, d in enumerate(DRIVERS)
}

SECTOR_COUNT: int = 3


# ---------------------------------------------------------------------------
# Deterministic best-lap formula
# ---------------------------------------------------------------------------


def _driver_index(driver: str) -> int:
    return DRIVERS.index(driver)


def _track_index(track: str) -> int:
    return TRACKS.index(track)


def _car_index(car: str) -> int:
    return CARS.index(car)


def _base_lap_ms(track: str, car: str, experiment: str, driver: str) -> int:
    """Deterministic historical best-lap formula shared by every driver.

    Layout: base shaped by (track, car); experiment offset is the same for
    everyone; driver offset is linear (100 ms per driver index) so a
    100-driver historical field spreads across ~10 s without the laps
    ballooning into absurd territory.
    """
    base_ms = 90_000 + _track_index(track) * 4_000 + _car_index(car) * 2_500
    exp_offset = -1_500 if experiment == "tuned" else 0
    d = _driver_index(driver)
    driver_offset = d * 100
    return base_ms + exp_offset + driver_offset


# Tuning knobs for Ludvík's warm-up curve. Combined they give him roughly
# one rank improvement every ~3 laps from a starting position of rank ~81,
# and he settles ahead of the fastest historical (`Alice`, offset 100 ms)
# once his offset hits the floor.
LUDVIK_DECAY_PER_LAP_MS: int = 30
LUDVIK_OFFSET_FLOOR_MS: int = -500


def _ludvik_lap_target_ms(
    track: str, car: str, experiment: str, current_lap: int
) -> int:
    """Per-lap pace target for the active driver.

    Ludvík starts deep in the historical field (rank ~80) and improves
    deterministically by `LUDVIK_DECAY_PER_LAP_MS` every lap, with a
    floor that eventually lands him at rank 1. Small jitter keeps PBs
    from feeling robotic. The whole point: keep the leaderboard *alive*
    so the collapse-and-scroll UI has motion to render.
    """
    base = _base_lap_ms(track, car, experiment, ACTIVE_DRIVER)
    starting_offset = 8_000
    decay = min(current_lap * LUDVIK_DECAY_PER_LAP_MS, starting_offset + 800)
    offset = max(LUDVIK_OFFSET_FLOOR_MS, starting_offset - decay)
    jitter = ((current_lap * 137) % 200) - 100
    return base + offset + jitter


# ---------------------------------------------------------------------------
# Active-driver state
# ---------------------------------------------------------------------------

# key = (track, car, experiment)
# value = {
#   "lap_start_epoch": float (time.time()),
#   "current_lap": int (1-based),
#   "best_lap_ms": int | None,
#   "best_lap_number": int | None,
#   "current_lap_ms": int,
# }
_LUDVIK_STATE: dict[tuple[str, str, str], dict[str, int | float | None]] = {}

# Deterministic "lap on which each historical's best was set" — purely cosmetic
# annotation shown next to the best-lap time in the UI. Generated for every
# historical driver; the active driver (index 0) gets a live-tracked value
# instead and is therefore excluded.
_HISTORICAL_BEST_LAP_NUMBERS: dict[str, int] = {
    d: ((i * 3) % 17) + 1 for i, d in enumerate(DRIVERS) if d != ACTIVE_DRIVER
}


def _get_or_init_state(
    key: tuple[str, str, str], now: float
) -> dict[str, int | float | None]:
    """Seed a brand-new (track, car, experiment) group on first request.

    `current_lap = 1`, no best yet, lap target computed from the formula.
    `lap_start_epoch` is `now` so the running clock starts at zero from
    the perspective of the first caller — they see the active driver at
    the very top of his out-lap, not partway through.
    """
    state = _LUDVIK_STATE.get(key)
    if state is None:
        track, car, experiment = key
        first_target = _ludvik_lap_target_ms(track, car, experiment, 1)
        state = {
            "lap_start_epoch": now,
            "current_lap": 1,
            "best_lap_ms": None,
            "best_lap_number": None,
            "current_lap_ms": first_target,
        }
        _LUDVIK_STATE[key] = state
    return state


def _advance_state(
    key: tuple[str, str, str], now: float
) -> tuple[dict[str, int | float | None], int]:
    """Roll laps forward until `elapsed < current_lap_ms`. Returns the
    state and the integer elapsed-in-current-lap in milliseconds.

    Drift correction: we advance `lap_start_epoch` by the lap's target
    duration (not to `now`), so the leftover from one lap carries into
    the next — matches real-world timing.
    """
    track, car, experiment = key
    state = _get_or_init_state(key, now)

    while True:
        lap_start = float(state["lap_start_epoch"] or 0.0)
        current_lap_ms = int(state["current_lap_ms"] or 0)
        elapsed_ms = int((now - lap_start) * 1000)
        if elapsed_ms < current_lap_ms:
            return state, elapsed_ms

        # Lap completed. Update best (if faster), advance.
        prev_best = state["best_lap_ms"]
        completed_lap_number = int(state["current_lap"] or 1)
        if prev_best is None or current_lap_ms < int(prev_best):
            state["best_lap_ms"] = current_lap_ms
            state["best_lap_number"] = completed_lap_number

        state["lap_start_epoch"] = lap_start + current_lap_ms / 1000.0
        state["current_lap"] = completed_lap_number + 1
        state["current_lap_ms"] = _ludvik_lap_target_ms(
            track, car, experiment, int(state["current_lap"])
        )


# ---------------------------------------------------------------------------
# Sector math
# ---------------------------------------------------------------------------


def _ludvik_sector_thresholds(current_lap_ms: int) -> tuple[int, int, int]:
    """End-of-sector thresholds (cumulative ms) using Ludvík's own splits.

    `(t0, t1, t2)` — once `elapsed >= t0` he has *completed* sector 0,
    once `elapsed >= t1` he has completed sector 1, and `t2 == lap_ms`
    is the lap line.
    """
    s0, s1, _s2 = SPLITS[ACTIVE_DRIVER]
    t0 = int(current_lap_ms * s0)
    t1 = int(current_lap_ms * (s0 + s1))
    t2 = int(current_lap_ms)
    return t0, t1, t2


def _completed_sector_count(elapsed_ms: int, current_lap_ms: int) -> int:
    """How many sectors has the active driver fully completed this lap?

    0 → still in sector 0. 1 → cleared sector 0, in sector 1. 2 → cleared
    sectors 0 and 1, in sector 2. (Sector 2 ends with the lap rollover so
    we don't return 3 here.)
    """
    t0, t1, _t2 = _ludvik_sector_thresholds(current_lap_ms)
    if elapsed_ms < t0:
        return 0
    if elapsed_ms < t1:
        return 1
    return 2


def _cumulative_at_boundary_splits(
    splits: tuple[float, float, float], lap_ms: int, completed_sectors: int
) -> int:
    """Sum of the first `completed_sectors` of `lap_ms * splits`."""
    total = 0.0
    for i in range(completed_sectors):
        total += lap_ms * splits[i]
    return int(total)


def _cumulative_at_boundary(
    driver: str, lap_ms: int, completed_sectors: int
) -> int:
    """Sim-only convenience over `_cumulative_at_boundary_splits` that looks
    `splits` up by driver name in the static `SPLITS` table.
    """
    return _cumulative_at_boundary_splits(SPLITS[driver], lap_ms, completed_sectors)


def ghost_time_for_splits(
    splits: tuple[float, float, float],
    best_lap_ms: int,
    active_sector: int,
    elapsed_ms: int,
    sector_start_ms: int,
    sector_end_ms: int,
) -> int:
    """Estimate a historical driver's lap-elapsed time at the active
    driver's *current map position* on the lap, parameterised by the
    historical driver's sector `splits` instead of looking them up in
    the sim's static `SPLITS` table.

    Public — imported by `leaderboard_real.py` so the real-mode path
    reuses the same ghost-interpolation math as the simulator. Real
    mode passes equal `(1/3, 1/3, 1/3)` splits because the lake doesn't
    expose per-driver sector breakdowns.
    """
    if sector_end_ms <= sector_start_ms:
        f = 0.0
    else:
        f = (elapsed_ms - sector_start_ms) / (sector_end_ms - sector_start_ms)
        if f < 0.0:
            f = 0.0
        elif f > 1.0:
            f = 1.0
    cum_start = _cumulative_at_boundary_splits(splits, best_lap_ms, active_sector)
    sector_duration = best_lap_ms * splits[active_sector]
    return int(cum_start + sector_duration * f)


def _ghost_time_for_driver(
    driver: str,
    best_lap_ms: int,
    active_sector: int,
    elapsed_ms: int,
    sector_start_ms: int,
    sector_end_ms: int,
) -> int:
    """Sim-only wrapper that resolves `splits` from the static `SPLITS`
    table by driver name, then delegates to `ghost_time_for_splits`.
    """
    return ghost_time_for_splits(
        SPLITS[driver],
        best_lap_ms,
        active_sector,
        elapsed_ms,
        sector_start_ms,
        sector_end_ms,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def make_local_dev_live_positions() -> list[dict[str, object]]:
    """Build the full 60-row response for LOCAL_DEV_MODE.

    Per group of 5 rows (track, car, experiment):
      1. Advance the active driver's lap state for `now`.
      2. Determine which sector he's in and his fractional progress.
      3. Compute ghost-interpolated `current_lap_time_ms` for each
         historical driver, and his actual elapsed for himself.
      4. Rank all 5 by cumulative-at-completed-sector-boundary. Until
         the first sector is completed this lap, the active driver sits
         at the bottom (rank 5) since there's no comparison yet.
    """
    now = time.time()
    out: list[dict[str, object]] = []
    for track in TRACKS:
        for car in CARS:
            for experiment in EXPERIMENTS:
                rows = _build_group(track, car, experiment, now)
                out.extend(rows)
    return out


def _build_group(
    track: str, car: str, experiment: str, now: float
) -> list[dict[str, object]]:
    key = (track, car, experiment)
    state, elapsed_ms = _advance_state(key, now)
    current_lap_ms = int(state["current_lap_ms"] or 0)
    current_lap = int(state["current_lap"] or 1)
    ludvik_best = state["best_lap_ms"]

    ludvik_best_lap_number = state["best_lap_number"]

    completed = _completed_sector_count(elapsed_ms, current_lap_ms)
    # Active sector index for ghost interpolation is the sector he's
    # currently *in*: 0, 1, or 2.
    active_sector = completed if completed < SECTOR_COUNT else SECTOR_COUNT - 1

    t0, t1, t2 = _ludvik_sector_thresholds(current_lap_ms)
    if active_sector == 0:
        sector_start_ms, sector_end_ms = 0, t0
    elif active_sector == 1:
        sector_start_ms, sector_end_ms = t0, t1
    else:
        sector_start_ms, sector_end_ms = t1, t2

    # Build rows (unranked).
    rows: list[dict[str, object]] = []
    for driver in DRIVERS:
        is_active = driver == ACTIVE_DRIVER
        best_lap_number_field: int | None
        if is_active:
            current_lap_time_ms = max(0, int(elapsed_ms))
            best_lap_ms_field = int(ludvik_best) if ludvik_best is not None else None
            best_lap_number_field = (
                int(ludvik_best_lap_number)
                if ludvik_best_lap_number is not None
                else None
            )
            current_lap_field: int | None = current_lap
        else:
            hist_best = _base_lap_ms(track, car, experiment, driver)
            best_lap_ms_field = hist_best
            best_lap_number_field = _HISTORICAL_BEST_LAP_NUMBERS[driver]
            current_lap_field = None
            # Edge case: brand-new lap, elapsed effectively zero -> show 0
            # for everyone rather than tiny rounding artifacts.
            if elapsed_ms <= 0:
                current_lap_time_ms = 0
            else:
                current_lap_time_ms = _ghost_time_for_driver(
                    driver,
                    hist_best,
                    active_sector,
                    elapsed_ms,
                    sector_start_ms,
                    sector_end_ms,
                )
        rows.append(
            {
                "track": track,
                "car": car,
                "experiment": experiment,
                "driver": driver,
                "best_lap_ms": best_lap_ms_field,
                "best_lap_number": best_lap_number_field,
                "is_active": is_active,
                "current_lap": current_lap_field,
                "current_lap_time_ms": current_lap_time_ms,
                # rank filled in below
                "rank": 0,
            }
        )

    _rank_group(rows, current_lap_ms, completed)
    return rows


def _rank_group(
    rows: list[dict[str, object]], current_lap_ms: int, completed: int
) -> None:
    """Server-computed rank within a single (track, car, experiment) group.

    Ranking is by each row's `current_lap_time_ms` ascending — for the
    active driver this is his actual elapsed, for the historicals it's
    the ghost-interpolated estimate at the active driver's current map
    position. This keeps the rank consistent with the displayed At
    Position column, so the gap-to-neighbour deltas never go negative.
    """
    rows.sort(key=lambda r: int(r["current_lap_time_ms"] or 0))
    for i, r in enumerate(rows):
        r["rank"] = i + 1


# ---------------------------------------------------------------------------
# Public helpers for real-mode (`leaderboard_real.py`)
# ---------------------------------------------------------------------------

# Equal sector splits used by the real-mode path. The lake doesn't expose
# per-driver sector breakdowns, so we treat every driver as a uniform-pace
# car. Combined with `normalizedCarPosition` from the live telemetry, this
# is enough to ghost-interpolate each historical's "at position" time.
EQUAL_SPLITS: tuple[float, float, float] = (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)


def rank_group(rows: list[dict[str, object]]) -> None:
    """Public alias for `_rank_group`. Real-mode rows always carry a
    populated `current_lap_time_ms`, so the unused `current_lap_ms` and
    `completed` parameters are dropped from the public surface.
    """
    rows.sort(key=lambda r: int(r["current_lap_time_ms"] or 0))
    for i, r in enumerate(rows):
        r["rank"] = i + 1


def sector_window_from_norm_pos(
    norm_pos: float, lap_ms: int
) -> tuple[int, int, int, int, int]:
    """Map a `normalizedCarPosition` ∈ [0, 1] onto an equal-sector window.

    Returns `(active_sector, elapsed_ms, sector_start_ms, sector_end_ms,
    completed)`:
      * `active_sector` ∈ {0, 1, 2}
      * `elapsed_ms` = `norm_pos * lap_ms` — the active driver's projected
        position-time along an equal-pace lap of length `lap_ms`.
      * `sector_start_ms`, `sector_end_ms` — bounds of the active sector
        in the equal-split layout.
      * `completed` — how many sectors the active driver has fully cleared.

    Used by `leaderboard_real.py` to feed `ghost_time_for_splits()`.
    """
    if norm_pos < 0.0:
        norm_pos = 0.0
    elif norm_pos >= 1.0:
        # Strictly < 1.0 keeps us in sector 2 on the lap line. A norm_pos
        # of exactly 1.0 arrives at lap rollover and is better treated as
        # "end of sector 2" than "start of a phantom sector 3".
        norm_pos = 0.9999
    active_sector = int(norm_pos * 3)
    if active_sector > 2:
        active_sector = 2
    sector_start_ms = int(lap_ms * active_sector / 3)
    sector_end_ms = int(lap_ms * (active_sector + 1) / 3)
    elapsed_ms = int(lap_ms * norm_pos)
    completed = active_sector
    return active_sector, elapsed_ms, sector_start_ms, sector_end_ms, completed
