"""Gate-state computation helpers shared by the snapshot-rebuild path and
the per-tick Kafka-thread path.

Both `routes/leaderboard_real.py` (snapshot rebuild) and `live_telemetry.py`
(per-tick `_record_message`) must compute the active driver's
`last_gate_state` colour from the SAME formula. Originally this lived in
`leaderboard_real.py` only, but pulling it back into `live_telemetry.py`
created a circular import (live_telemetry ← routes/leaderboard_real ←
live_telemetry). Moving it to a neutral leaf module breaks the cycle and
guarantees both call sites use one implementation.

The functions here are intentionally pure: they take inputs and return
results, with no side effects and no dependencies on `live_telemetry`'s
module-level caches. `leaderboard_real` re-exports `compute_last_gate_state`
so older import paths keep working.

The formula (locked by `dev-planning/sc-71954-dual-mode-leaderboard/spec.md`):

* `last_gate_index` is the highest index `i` for which
  `active_gate_times[i] is not None` on the current lap. `None` before
  the active driver crosses gate 1.
* `last_gate_delta_ms = active_gate_times[i*] - median(historicals.gate_vector[i*])`.
  Positive => active is slower than the historical median; negative =>
  faster.
* `last_gate_state` is `"neutral"` when `|last_gate_delta_ms| <= 50` ms
  (the locked neutral band), `"behind"` when the delta is positive
  outside that band, `"ahead"` when it is negative outside that band.
  Empty historicals or unknown gate yields `(i*, "neutral", None)` —
  signals "cold cache" without painting a colour.

`compute_delta_at_last_gate_ms_per_historical` returns the per-historical
inline deltas the active-mutation envelope carries on the wire
(`active.row.historical_deltas[driver]` and snapshot rows'
`delta_at_last_gate_ms` column).
"""

from __future__ import annotations

from typing import Literal

# Re-export `_HistoricalEntry` lazily-typed via TYPE_CHECKING to avoid the
# circular import at runtime. Callers pass `_HistoricalEntry` instances in.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .live_telemetry import _HistoricalEntry


# Neutral band locked at 50 ms by spec §5.3 (open question Q1 resolved at
# the default). Outside this window the state is `"ahead"` (active faster)
# or `"behind"` (active slower) per the sign of `last_gate_delta_ms`.
GATE_NEUTRAL_BAND_MS = 50


def latest_crossed_gate(gate_times_ms: list[int | None]) -> int | None:
    """Return the highest index with a populated gate time, or `None`."""
    for i in range(len(gate_times_ms) - 1, -1, -1):
        if gate_times_ms[i] is not None:
            return i
    return None


def _median_sorted(values: list[int]) -> float:
    """Median of a non-empty pre-sortable list. Returns a float so a
    two-element input doesn't silently truncate the midpoint to int."""
    if not values:
        raise ValueError("median of empty sequence")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def compute_last_gate_state(
    active_gate_times: list[int | None],
    historicals: "dict[str, _HistoricalEntry] | None",
    gate_count: int,
) -> tuple[
    int | None,
    Literal["ahead", "behind", "neutral"] | None,
    int | None,
]:
    """Compute `(last_gate_index, last_gate_state, last_gate_delta_ms)`.

    Median-vs-active rule with a 50 ms neutral band — see module docstring
    for the locked formula. Returns `(None, None, None)` when no gate
    has been crossed yet. Returns `(i*, "neutral", None)` when historicals
    are missing or have no usable gate vector for index `i*` — cold cache
    paints no colour rather than guessing.
    """
    i_star = latest_crossed_gate(active_gate_times)
    if i_star is None:
        return None, None, None
    active_t = active_gate_times[i_star]
    if active_t is None:
        return None, None, None
    if not historicals:
        return i_star, "neutral", None

    hist_ts: list[int] = []
    for h in historicals.values():
        vec = h.gate_vector
        if len(vec) == gate_count and vec[i_star] is not None:
            hist_ts.append(int(vec[i_star]))
    if not hist_ts:
        return i_star, "neutral", None

    median_t = _median_sorted(hist_ts)
    delta_ms = int(round(active_t - median_t))
    state: Literal["ahead", "behind", "neutral"]
    if abs(delta_ms) <= GATE_NEUTRAL_BAND_MS:
        state = "neutral"
    elif delta_ms > 0:
        state = "behind"
    else:
        state = "ahead"
    return i_star, state, delta_ms


def compute_per_historical_deltas(
    active_gate_times: list[int | None],
    historicals: "dict[str, _HistoricalEntry] | None",
    gate_count: int,
) -> dict[str, int]:
    """Return `{folded_driver: delta_at_last_gate_ms}` for every historical.

    `delta_at_last_gate_ms = active_gate_times[i*] - historical.gate_vector[i*]`.
    Sign convention matches `last_gate_delta_ms` on the active row:
    **positive = active is slower than the historical at that gate**.

    Missing historicals, missing gate vectors, or "no gate crossed yet"
    all yield an empty dict — the wire carries `None` deltas for those
    cases instead of fabricating zeros.
    """
    out: dict[str, int] = {}
    i_star = latest_crossed_gate(active_gate_times)
    if i_star is None or not historicals:
        return out
    active_t = active_gate_times[i_star]
    if active_t is None:
        return out
    for folded_driver, h in historicals.items():
        vec = h.gate_vector
        if len(vec) != gate_count or vec[i_star] is None:
            continue
        out[folded_driver] = int(active_t) - int(vec[i_star])
    return out


def to_display_name(folded_key: str, lookup: dict[str, str]) -> str:
    """Map a folded driver key back to the Mongo display-case name.

    Falls back to the title-cased folded key (e.g. `"tomas"` →
    `"Tomas"`) when the lookup has no entry. Empty input returns
    empty. The caller is expected to cache the `lookup` map (built via
    `_build_driver_name_lookup(mongo)`) so this is a hot-path-cheap
    dict.get with a fallback.
    """
    if not folded_key:
        return ""
    display = lookup.get(folded_key)
    if display:
        return display
    return folded_key[:1].upper() + folded_key[1:]
