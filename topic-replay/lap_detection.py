"""Detect a single complete lap window from a list of captured raw rows.

Lap boundaries are detected via `completedLaps` transitions where the new
value is exactly `prev + 1`. This is AC's canonical "I just crossed the
start/finish line" signal and is robust against iCurrentTime resets that
happen on pit entry / session-time → lap-time transitions (those don't
increment `completedLaps`).

The returned tuple is a half-open `[start_idx, end_idx)` window — exactly
the interval between two consecutive `completedLaps` increments. Picks the
FASTEST such window (smallest end-start ticks) so we always loop on a clean
flying lap rather than an out lap or a slow first lap.

Why not `iCurrentTime` resets: AC resets `iCurrentTime` whenever the lap
context changes — not only at lap end. A capture that included pit-lane
or session-time data produced 47 s "laps" that were actually partial
windows between two non-lap-boundary resets. `completedLaps` only ever
goes up at the real start/finish crossing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# A captured lap shorter than this many ticks is almost certainly an
# out-lap or partial window — drop it from the candidate list so we don't
# loop a 5-second blip.
MIN_TICKS_FOR_LAP = 600


class LapDetectionError(RuntimeError):
    """Raised when no full lap is present in the capture."""


def _completed_laps(row: dict) -> int | None:
    """Pluck `completedLaps` from a row whether it's top-level (already a
    decoded payload) or nested under `value` (full JSONL capture row)."""
    if "completedLaps" in row:
        return row["completedLaps"]
    value = row.get("value")
    if isinstance(value, dict):
        return value.get("completedLaps")
    return None


def find_single_lap(raw_rows: list[dict]) -> tuple[int, int]:
    """Return a half-open `[start_idx, end_idx)` window covering the longest
    contiguous run of consecutive `completedLaps` increments in the capture.

    A "contiguous run" is a maximal chain of increment indices `(a, b, c, …)`
    where every adjacent pair `(prev, next)` has `next - prev` ≥
    `MIN_TICKS_FOR_LAP` ticks. The replay loops the *entire* run so the
    active driver plays the multi-lap progression that was captured
    (typically slow first lap → fastest flying lap), then re-starts. A
    single completed lap is still valid (run length = 1).

    Raises `LapDetectionError` if no valid full lap is found.
    """
    # Scan once: record `+1` increments AND any `curr < prev` reset as a
    # discontinuity marker. Resets back to 0 (e.g. exit garage, restart
    # lap) MUST split runs; a reset in the middle of two increments means
    # those laps are not contiguous flying laps even though both
    # increments happened.
    increments: list[int] = []
    reset_indices: list[int] = []
    for i in range(1, len(raw_rows)):
        prev = _completed_laps(raw_rows[i - 1])
        curr = _completed_laps(raw_rows[i])
        if prev is None or curr is None:
            continue
        if curr == prev + 1:
            increments.append(i)
        elif curr < prev:
            reset_indices.append(i)

    reset_set = set(reset_indices)

    def _has_reset_between(a: int, b: int) -> bool:
        return any(a < r < b for r in reset_indices)

    runs: list[list[int]] = []
    current: list[int] = []
    for idx in increments:
        if not current:
            current = [idx]
            continue
        gap_ok = idx - current[-1] >= MIN_TICKS_FOR_LAP
        reset_between = _has_reset_between(current[-1], idx)
        if not gap_ok or reset_between:
            if len(current) >= 2:
                runs.append(current)
            current = [idx]
        else:
            current.append(idx)
    if len(current) >= 2:
        runs.append(current)

    if not runs:
        raise LapDetectionError(
            f"No full lap found in capture (>= {MIN_TICKS_FOR_LAP} ticks "
            f"between consecutive completedLaps increments). Found "
            f"{len(increments)} increments total. Capture more data."
        )

    # Pick the SINGLE SLOWEST lap across all contiguous runs — the
    # replay loops one clean lap forever. Slowest is preferred so the
    # active driver lands mid-pack against the lake historicals,
    # producing visible rank movement gate-by-gate as he overtakes /
    # gets overtaken. Earlier "fastest" pick made him rank 1 every lap.
    best: tuple[int, int, int] | None = None  # (length, start, end)
    for run in runs:
        for a, b in zip(run, run[1:]):
            length = b - a
            if length < MIN_TICKS_FOR_LAP:
                continue
            if best is None or length > best[0]:
                best = (length, a, b)
    if best is None:
        raise LapDetectionError(
            "No single-lap window passed the MIN_TICKS_FOR_LAP filter."
        )
    _length, start_idx, end_idx = best
    logger.info(
        "Lap window detected (single slowest): start_idx=%d end_idx=%d ticks=%d",
        start_idx,
        end_idx,
        end_idx - start_idx,
    )
    return start_idx, end_idx
