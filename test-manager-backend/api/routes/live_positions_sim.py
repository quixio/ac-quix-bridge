"""Multi-driver live-positions simulator for the Analysis Leaderboard.

LOCAL_DEV_MODE only. Pure functions + a single module-level dict for the
active driver's running lap state per (track, car, experiment). No I/O,
no Kafka, no DB. Returns a flat list of `LivePositionEntry` dicts ready
for `pydantic`-validation by the route.

Why this shape:

* The product question is "where do I sit in a group of historical
  drivers as I progress through the lap?". The display is a paginated
  table per (track, car, experiment) group; the active driver's rank
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

Dual model (spec §8.5): the sim keeps the 3-sector rank function for
ranking purposes (cheap, stable, no behaviour change for the rank
column) but additionally tracks the active driver's 10-checkpoint-gate
crossings to drive the new `last_gate_state` colour. The two models
serve different purposes — sector rank determines vertical order;
gate state drives the "At Position" colour cue.

Public API: `make_local_dev_live_positions()`.
"""

from __future__ import annotations

import time
from typing import Any

from ..live_telemetry import GATE_COUNT, _HistoricalEntry, _update_gate_times

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
# Deterministic per-historical gate-vector generator (spec §5.7)
# ---------------------------------------------------------------------------
#
# Real mode caches `_HistoricalEntry` objects queried from the lake. The sim
# can't call the lake, so it synthesises equivalent entries from a small
# set of perturbation profiles. Each profile distorts the equal-split
# baseline `gate_vector[i] = best_ms * (i+1)/10` so the active driver's
# colour visibly cycles through "ahead" → "neutral" → "behind" → "neutral"
# as he crosses successive gates.
#
# The cache shape matches the real-mode `_gate_vectors_cache`:
#   {(track, car, experiment): {driver_folded: _HistoricalEntry}}
# Driver names are folded the same way (`_fold_driver_name`) for parity.

# Four perturbation profiles, each a list of per-gate offsets as a
# fraction of `best_ms`. Sum across the 10 entries is zero so the lap
# total isn't perturbed. The profiles are intentionally diverse: profile
# 0 is fast early then slow late, profile 1 the inverse, etc. Indexing
# is `_GATE_PERTURBATIONS[profile][gate_idx]`. Values scaled so a
# 90-second lap shifts a gate by ~±400-800 ms — visibly different from
# the active driver's elapsed but not so extreme that the rank order
# becomes pathological. Lengths match GATE_COUNT=10.
_GATE_PERTURBATIONS: list[list[float]] = [
    # Profile 0: fast first half, slow second half. Sum = 0.0.
    [-0.006, -0.004, -0.002, 0.0, 0.002, 0.004, 0.003, 0.002, 0.001, 0.0],
    # Profile 1: slow first half, fast second half. Sum = 0.0.
    [0.006, 0.004, 0.002, 0.0, -0.002, -0.004, -0.003, -0.002, -0.001, 0.0],
    # Profile 2: mid-lap dip. Sum = 0.0.
    [0.004, 0.001, -0.003, -0.004, 0.0, 0.003, 0.001, -0.002, 0.0, 0.0],
    # Profile 3: opposite of profile 2. Sum = 0.0.
    [-0.004, -0.001, 0.003, 0.004, 0.0, -0.003, -0.001, 0.002, 0.0, 0.0],
]


def _sim_gate_vector(driver_idx: int, best_lap_ms: int) -> list[int]:
    """Build a deterministic monotone gate vector for one historical.

    `driver_idx` picks a profile (mod 4) and a per-driver phase shift so
    consecutive drivers don't all show the same colour pattern. The
    result is monotonically non-decreasing because the perturbations
    only add a small fraction of `best_lap_ms` to each cumulative slot
    and we enforce monotonicity at the end.
    """
    profile = _GATE_PERTURBATIONS[driver_idx % len(_GATE_PERTURBATIONS)]
    phase = driver_idx % GATE_COUNT
    vector: list[int] = []
    for i in range(GATE_COUNT):
        baseline = best_lap_ms * (i + 1) / GATE_COUNT
        # Rotate the perturbation by phase so two drivers on the same
        # profile aren't identical.
        offset = profile[(i + phase) % GATE_COUNT] * best_lap_ms
        vector.append(int(baseline + offset))
    # Force monotonic non-decreasing.
    for i in range(1, GATE_COUNT):
        if vector[i] < vector[i - 1]:
            vector[i] = vector[i - 1]
    # Force gate_vector[-1] == best_lap_ms so the lap total stays exact.
    vector[GATE_COUNT - 1] = int(best_lap_ms)
    return vector


def _fold_driver_name_sim(name: str) -> str:
    """NFKD + ASCII-lowercase fold, mirroring `leaderboard_real._fold_driver_name`.

    Re-implemented locally so the sim module doesn't depend on
    `leaderboard_real` (which would cycle via `live_telemetry`).
    """
    import unicodedata

    if not name:
        return ""
    folded = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    return folded or name.lower()


def _build_sim_gate_vectors_cache() -> dict[
    tuple[str, str, str], dict[str, _HistoricalEntry]
]:
    """Synthesise the full simulator gate-vectors cache up front.

    Generated once at module import time. The active driver "Ludvík" is
    *not* included — his entry is recomputed on every lap from his live
    elapsed (he's the running clock, not a historical).
    """
    cache: dict[tuple[str, str, str], dict[str, _HistoricalEntry]] = {}
    for track in TRACKS:
        for car in CARS:
            for experiment in EXPERIMENTS:
                key = (track, car, experiment)
                group: dict[str, _HistoricalEntry] = {}
                for idx, driver in enumerate(DRIVERS):
                    if driver == ACTIVE_DRIVER:
                        continue
                    best = _base_lap_ms(track, car, experiment, driver)
                    group[_fold_driver_name_sim(driver)] = _HistoricalEntry(
                        best_lap_ms=best,
                        best_lap_number=_HISTORICAL_BEST_LAP_NUMBERS_PLACEHOLDER.get(
                            driver, 1
                        ),
                        gate_vector=_sim_gate_vector(idx, best),
                    )
                cache[key] = group
    return cache


# `_HISTORICAL_BEST_LAP_NUMBERS` is defined further down (after
# `_LUDVIK_STATE`). To avoid a forward-reference we keep a placeholder
# the cache builder reads at runtime — it's wired up after that constant
# is defined below.
_HISTORICAL_BEST_LAP_NUMBERS_PLACEHOLDER: dict[str, int] = {}


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
#   "gate_times_ms": list[int|None] (length GATE_COUNT),
#   "last_norm_pos": float,
#   "last_gate_index": int | None,
#   "last_gate_state": str | None,
#   "last_gate_delta_ms": int | None,
# }
_LUDVIK_STATE: dict[tuple[str, str, str], dict[str, Any]] = {}

# Deterministic "lap on which each historical's best was set" — purely cosmetic
# annotation shown next to the best-lap time in the UI. Generated for every
# historical driver; the active driver (index 0) gets a live-tracked value
# instead and is therefore excluded.
_HISTORICAL_BEST_LAP_NUMBERS: dict[str, int] = {
    d: ((i * 3) % 17) + 1 for i, d in enumerate(DRIVERS) if d != ACTIVE_DRIVER
}
# Wire the constant into the forward-declared placeholder used by
# `_build_sim_gate_vectors_cache` (defined further up; the cache itself
# is materialised on first call to `_sim_gate_vectors_cache()`).
_HISTORICAL_BEST_LAP_NUMBERS_PLACEHOLDER.update(_HISTORICAL_BEST_LAP_NUMBERS)


# Lazy-built so module import doesn't pay the construction cost when the
# server runs in real mode. Cleared/never used outside LOCAL_DEV_MODE.
_SIM_GATE_VECTORS_CACHE: (
    dict[tuple[str, str, str], dict[str, _HistoricalEntry]] | None
) = None


def _sim_gate_vectors_cache() -> dict[
    tuple[str, str, str], dict[str, _HistoricalEntry]
]:
    """Return the (lazy-built) simulator gate-vectors cache."""
    global _SIM_GATE_VECTORS_CACHE
    if _SIM_GATE_VECTORS_CACHE is None:
        _SIM_GATE_VECTORS_CACHE = _build_sim_gate_vectors_cache()
    return _SIM_GATE_VECTORS_CACHE


def _get_or_init_state(key: tuple[str, str, str], now: float) -> dict[str, Any]:
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
            "gate_times_ms": [None] * GATE_COUNT,
            "last_norm_pos": 0.0,
            "last_gate_index": None,
            "last_gate_state": None,
            "last_gate_delta_ms": None,
        }
        _LUDVIK_STATE[key] = state
    return state


def _advance_state(key: tuple[str, str, str], now: float) -> tuple[dict[str, Any], int]:
    """Roll laps forward until `elapsed < current_lap_ms`. Returns the
    state and the integer elapsed-in-current-lap in milliseconds.

    Drift correction: we advance `lap_start_epoch` by the lap's target
    duration (not to `now`), so the leftover from one lap carries into
    the next — matches real-world timing.

    On lap rollover this resets `gate_times_ms` to `[None]*GATE_COUNT`
    AND clears the sticky `last_gate_*` fields, mirroring the real-mode
    rollover branch in `live_telemetry._record_message` (spec §8.7).
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
            track,
            car,
            experiment,
            int(state["current_lap"]),
        )
        # Lap rollover: clear gate bookkeeping per spec §8.7.
        state["gate_times_ms"] = [None] * GATE_COUNT
        state["last_norm_pos"] = 0.0
        state["last_gate_index"] = None
        state["last_gate_state"] = None
        state["last_gate_delta_ms"] = None


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


def _cumulative_at_boundary(driver: str, lap_ms: int, completed_sectors: int) -> int:
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

    # Gate bookkeeping for the active driver. `_update_gate_times`
    # stamps any 5%/10%/.../100% gates crossed since the previous tick
    # (spec §5.1 + §5.7). Note: this runs in parallel to the existing
    # 3-sector ranking (§8.5: keep both models; sectors drive rank,
    # gates drive colour state).
    norm_pos = (
        min(0.9999, max(0.0, elapsed_ms / current_lap_ms)) if current_lap_ms else 0.0
    )
    gate_times = list(state["gate_times_ms"] or [None] * GATE_COUNT)
    if len(gate_times) != GATE_COUNT:
        gate_times = [None] * GATE_COUNT
    prev_norm = float(state["last_norm_pos"] or 0.0)
    _update_gate_times(gate_times, prev_norm, norm_pos, elapsed_ms)
    state["gate_times_ms"] = gate_times
    state["last_norm_pos"] = norm_pos

    # Compute or re-emit `last_gate_*` based on whether a new gate has
    # been crossed since last poll. Stickiness is required (spec §5.4)
    # so the colour holds between crossings. Cache lookup is done once
    # per group using the simulator's pre-built gate vectors.
    cache = _sim_gate_vectors_cache()
    group_historicals = cache.get(key, {})
    new_i_star = _latest_crossed_gate_sim(gate_times)
    prev_i_star = state.get("last_gate_index")
    if new_i_star is not None and new_i_star != prev_i_star:
        last_index, last_state, last_delta = _compute_last_gate_state_sim(
            gate_times, group_historicals
        )
        state["last_gate_index"] = last_index
        state["last_gate_state"] = last_state
        state["last_gate_delta_ms"] = last_delta
    else:
        last_index = int(prev_i_star) if isinstance(prev_i_star, int) else None
        prev_state = state.get("last_gate_state")
        last_state = (
            prev_state if prev_state in ("ahead", "behind", "neutral") else None
        )
        prev_delta = state.get("last_gate_delta_ms")
        last_delta = int(prev_delta) if isinstance(prev_delta, int) else None

    # Per-historical inline deltas (spec §7.2). Computed once per group
    # so every historical row in this group carries the same active-row
    # `last_gate_index` reference.
    from .. import gate_math

    per_historical_deltas = gate_math.compute_per_historical_deltas(
        gate_times, group_historicals or None, GATE_COUNT
    )

    # Build rows (unranked).
    rows: list[dict[str, object]] = []
    for driver in DRIVERS:
        is_active = driver == ACTIVE_DRIVER
        best_lap_number_field: int | None
        row_last_index: int | None
        row_last_state: str | None
        row_last_delta: int | None
        if is_active:
            current_lap_time_ms = max(0, int(elapsed_ms))
            best_lap_ms_field = int(ludvik_best) if ludvik_best is not None else None
            best_lap_number_field = (
                int(ludvik_best_lap_number)
                if ludvik_best_lap_number is not None
                else None
            )
            current_lap_field: int | None = current_lap
            row_last_index = last_index
            row_last_state = last_state
            row_last_delta = last_delta
        else:
            hist_best = _base_lap_ms(track, car, experiment, driver)
            best_lap_ms_field = hist_best
            best_lap_number_field = _HISTORICAL_BEST_LAP_NUMBERS[driver]
            current_lap_field = None
            # Historical rows echo the active driver's last_gate_index
            # so the frontend's delta column lines up; the per-row
            # delta itself comes from `per_historical_deltas`.
            row_last_index = last_index
            row_last_state = None
            row_last_delta = None
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
        row_delta_at_last_gate_ms: int | None = None
        if not is_active:
            folded = _fold_driver_name_sim(driver)
            row_delta_at_last_gate_ms = per_historical_deltas.get(folded)
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
                "last_gate_index": row_last_index,
                "last_gate_state": row_last_state,
                "last_gate_delta_ms": row_last_delta,
                "delta_at_last_gate_ms": row_delta_at_last_gate_ms,
            }
        )

    _rank_group(rows, current_lap_ms, completed)
    return rows


def _latest_crossed_gate_sim(gate_times: list[int | None]) -> int | None:
    """Local mirror of `gate_math.latest_crossed_gate` — kept here only so
    the sim file remains self-contained for readers. Identical formula.
    """
    for i in range(len(gate_times) - 1, -1, -1):
        if gate_times[i] is not None:
            return i
    return None


def _compute_last_gate_state_sim(
    active_gate_times: list[int | None],
    historicals: dict[str, _HistoricalEntry],
) -> tuple[int | None, str | None, int | None]:
    """Median-vs-active gate-state computation with 50 ms neutral band.

    Delegates to the shared `api.gate_math.compute_last_gate_state` so
    sim and real paths cannot drift out of step. The dual-mode spec
    (§5.3) locks the median rule + 50 ms neutral band as the wire
    contract.
    """
    # Lazy import to avoid the routes → live_telemetry → routes import
    # cycle through `_HistoricalEntry`. `gate_math` is a leaf module so
    # this stays one-way (sim → gate_math only).
    from .. import gate_math

    return gate_math.compute_last_gate_state(
        active_gate_times, historicals or None, GATE_COUNT
    )


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


def rank_group(rows: list[dict[str, object]]) -> None:
    """Real-mode rank: sort by `rank_time_ms` (each row's cumulative lap
    time at the LAST gate the active driver crossed).

    For historicals that's `gate_vector[last_gate_index]` — sticky.
    For the active row that's his own iCurrentTime captured at the moment
    he crossed `last_gate_index` — also sticky.

    Sorting on this snapshot means active's rank only changes at gate
    crossings, not on every WS tick. Previously we sorted on
    `current_lap_time_ms` directly, which conflated live (active) and
    sticky (historicals) values and made the active drift from rank 1
    (small live ticks) to rank 13 (large live ticks) within a single lap.

    Falls back to `current_lap_time_ms` if `rank_time_ms` is absent (older
    callers + sim-mode rows).
    """
    def _key(r: dict[str, object]) -> int:
        v = r.get("rank_time_ms")
        if v is None or v == 0:
            v = r.get("current_lap_time_ms")
        return int(v or 0)
    rows.sort(key=_key)
    for i, r in enumerate(rows):
        r["rank"] = i + 1
