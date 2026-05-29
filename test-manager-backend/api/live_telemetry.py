"""Live-telemetry state store + Kafka consumer for the Ghost Lap leaderboard.

Two execution modes, switched by env:

  * `LOCAL_DEV_MODE=true`  → consumer thread NEVER runs. `get_active_driver()`
    returns a deterministic simulated lap so the frontend has motion to render
    without AC + Kafka.
  * `LIVE_TELEMETRY_ENABLED=true` (and `LOCAL_DEV_MODE` not `true`) →
    background thread runs a QuixStreams `Application` consuming both
    `ac-telemetry-raw` (high-frequency ticks) and `ac-telemetry-session`
    (per-session static metadata). The raw topic has no `track`, `carModel`,
    `driver`, or `experiment` fields; those live in the session topic
    (track/car/playerName) and in DCM (experiment, keyed by hostname).
    The consumer keeps per-hostname caches of both and enriches every raw
    payload before recording. Last message per `(track, car, driver)` is
    kept in a module-level dict guarded by an RLock. Stale entries
    (>10 s old) are treated as "no active driver".

Why a thread instead of an asyncio task: QuixStreams' consumer loop is
synchronous (`app.run()` blocks). The FastAPI event loop must keep handling
HTTP requests, so we isolate the Kafka loop in a daemon thread, signal it to
stop on shutdown, and join with a short timeout.

The endpoint layer is the only consumer of `get_active_driver()` /
`simulated_active_driver()` — keep those two helpers as the public API of
this module.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Topics the AC source publishes to. Hardcoded because the source's
# `app.yaml` declares these names and we don't want extra env vars.
#
# The raw topic carries high-frequency physics/graphics ticks but does NOT
# include `track`, `carModel`, `playerName`, or `experiment` — those come
# from the session topic (once per session) and the DCM (experiment config
# keyed by hostname). The consumer subscribes to both and enriches raw
# messages from a per-hostname cache before recording state. This mirrors
# the lake sink's `ac-telemetry-raw ⨝ ac-telemetry-config` enrichment.
#
# The consumer also subscribes to `ac-telemetry-config` — DCM's event
# stream — so changes made via Test Manager (driver swap, experiment
# rename, test delete) propagate in real-time without waiting for the
# next AC session message. The DCM events topic is additive: AC's
# `ac-telemetry-session` remains the source of truth for track / carModel
# / playerName (DCM only mirrors those after `session-config-bridge` has
# run, which may not have happened yet for an unlinked sim PC).
TOPIC = "ac-telemetry-raw"
SESSION_TOPIC = "ac-telemetry-session"
CONFIG_EVENTS_TOPIC = "ac-telemetry-config"

# Stale-entry threshold: a driver that hasn't sent a tick in this many seconds
# is no longer "active". 10 s comfortably absorbs a brief pause / network
# blip but won't keep a long-gone session pinned.
STALE_AFTER_S = 10.0

# Consumer group + offset reset. `latest` is the right choice for "what is
# happening right now" — we don't care about history.
#
# We subscribe to BOTH the raw topic (for live ticks) AND the session topic
# (for track/car/driver enrichment). Session messages are produced once per
# AC session, so on restart we won't have a cached session yet — until the
# next session change, raw ticks for an unknown hostname are dropped with a
# debug log. The session topic uses the source's hostname as Kafka key, so
# `auto_offset_reset="latest"` is acceptable: as long as the bridge is
# running when the user starts/restarts AC, the next session message will
# populate the cache before any noticeable amount of raw data flows. If
# this proves too lossy in practice we can switch the session subscription
# to `earliest` independently (different consumer or seek-on-assign).
CONSUMER_GROUP = "test-manager-backend-ghost-lap"

# Number of equally-sized checkpoint gates the lap is split into for the
# Live Sector Comparison table. 20 → gates at normalizedCarPosition 0.05,
# 0.10, ..., 0.95, 1.00. The 0% gate is implicit (always 0 ms) and is not
# stored, so `gate_times_ms[0]` corresponds to the 5% gate and `[19]` to
# the lap line.
GATE_COUNT = 20

# DCM experiment lookups: timeouts and a cache TTL fence. The TTL only
# matters as a safety net — the primary invalidation event is a new session
# message for the same hostname (which triggers a fresh fetch). 5 s timeout
# keeps a slow DCM from stalling the Kafka poll loop noticeably; the call
# happens on session-message arrival only, not on every tick.
DCM_TIMEOUT_S = 5.0
EXPERIMENT_CACHE_TTL_S = 300.0


def _update_gate_times(
    gate_times: list[int | None],
    prev_pos: float,
    new_pos: float,
    current_lap_time_ms: int,
) -> list[int]:
    """Stamp `current_lap_time_ms` into every gate whose position
    `(i+1)/GATE_COUNT` was crossed between `prev_pos` (exclusive) and
    `new_pos` (inclusive). Mutates `gate_times` in place and returns the
    list of newly-stamped gate indices (in ascending order) so the caller
    can compute the latest crossed gate without re-scanning the array.

    If a single tick straddles multiple gates (sim coarse / network
    hiccup), all of them get the same timestamp — acceptable for V1.
    A gate that already has a value is left untouched (first crossing
    wins, even if `normalized_position` wobbles backwards later).
    """
    crossed: list[int] = []
    for i in range(GATE_COUNT):
        if gate_times[i] is not None:
            continue
        boundary = (i + 1) / GATE_COUNT
        if new_pos >= boundary > prev_pos:
            gate_times[i] = current_lap_time_ms
            crossed.append(i)
    return crossed


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

_state_lock = threading.RLock()
_state: dict[tuple[str, str, str], dict[str, Any]] = {}

# Per-hostname enrichment caches, guarded by `_state_lock` (the same lock as
# `_state` — keeps the locking story trivial; sessions update is rare enough
# that contention with the hot raw-tick path is negligible).
#
# `_session_cache[hostname]` = {"track": ..., "carModel": ..., "playerName": ...}
# populated on every session message (one per AC session).
#
# `_experiment_cache[hostname]` = {"experiment": str, "driver": str,
#                                  "environment": str, "fetched_epoch": float}
# populated synchronously when a session message arrives (one DCM HTTP call
# per session change); refreshed if older than EXPERIMENT_CACHE_TTL_S. All
# three of `experiment` (content.experiment_id), `driver` (content.driver,
# the canonical "who is driving this test" set by Test Manager), and
# `environment` (content.environment, the lake's `environment` Hive
# partition) come from the same DCM experiment-config fetch — no extra HTTP
# calls vs the prior two-field cache. The environment value is required by
# the best-laps lake query (`environment` partitions the lake and missing
# it from the WHERE clause caused the previous "scans-too-wide / only-2-rows"
# bug).
_session_cache: dict[str, dict[str, str]] = {}
_experiment_cache: dict[str, dict[str, Any]] = {}

_consumer_thread: threading.Thread | None = None
_stop_event = threading.Event()

# ---------------------------------------------------------------------------
# Best-laps cache (Right-table source: Best Laps panel)
# ---------------------------------------------------------------------------
#
# Why this cache exists: `/api/v1/leaderboard/live-positions` is polled ~every
# 3.5 s while a user has the leaderboard tab open, but the underlying
# per-driver best laps in the configured lake table only change when a new fast lap
# completes. We refresh on the natural "something changed" signal — a new
# `ac-telemetry-session` message or a DCM config event — so the lake hit
# drops from "every poll" to "once per AC session start / DCM edit".
#
# Cache shape:
#   {(track, carModel, experiment, environment): {driver_folded: best_lap_ms}}
# Driver names are folded via `_fold_driver_name` (NFKD + ASCII lowercase)
# so a lake `"ludvik"` and a Mongo `"Ludvík"` collide on the same key.
# `best_lap_ms` is the per-driver `MIN(iBestTime) FILTER (WHERE iBestTime > 0)`
# straight from QuixLake — already in milliseconds, no Python reduction
# required (this replaces the previous flaky
# `MAX(timestamp_ms) - MIN(timestamp_ms)` aggregation).
#
# A `None` sentinel means "never refreshed yet" — distinct from "refreshed,
# but the lake had no rows" which is an empty dict. `build_live_positions`
# triggers a synchronous fallback refresh on `None`.
#
# Guarded by a dedicated `_best_laps_lock` rather than `_state_lock`
# because the lake queries take seconds and we must NOT block the raw-tick
# path. The refresh function builds the new dict outside the lock and only
# holds the lock long enough to swap the reference.
#
# Pairs with `_gate_vectors_cache` below: the best-laps cache powers the
# Right-table "Best Laps" panel (scalar per-driver best in ms), while the
# gate-vectors cache powers the Left-table "Live Sector Comparison" (full
# per-gate cumulative-time vectors required for the colour cue). Both
# refresh on the same triggers.


# Per-driver per-gate cumulative-time vectors of the historical best lap.
# Populated by `refresh_gate_vectors_cache` and consumed by the active-row
# colour computation in `_record_message` plus the snapshot-rebuild path
# in `routes/leaderboard_real.py`. The two consumers share the
# `gate_math.compute_last_gate_state` helper to stay in lockstep.
@dataclass(frozen=True)
class _HistoricalEntry:
    """Cached per-gate breakdown of one historical driver's best lap.

    * `best_lap_ms` — by definition equal to `gate_vector[19]` (the 100% /
      lap-line gate). Stored explicitly so the assembly code reads more
      naturally than `entry.gate_vector[19]`.
    * `best_lap_number` — 1-indexed lap number on which the best was set,
      shown in the UI's Best Lap column.
    * `gate_vector` — length-20 list of cumulative ms at the 5%, 10%, ...,
      100% gates of that historical's best lap, sorted by position. The
      implicit 0% gate is always 0 ms and not stored. Monotonically
      non-decreasing by construction (it's a cumulative-time vector).
    """

    best_lap_ms: int
    best_lap_number: int
    gate_vector: list[int] = field(default_factory=list)


_gate_vectors_lock = threading.RLock()
_gate_vectors_cache: dict[tuple[str, str, str], dict[str, _HistoricalEntry]] | None = (
    None
)

# Best-laps cache: per-driver best lap in milliseconds, keyed by the full
# (track, car, experiment, environment) tuple to match the new lake WHERE
# clause exactly. `None` until the first refresh runs (distinct from
# empty-but-refreshed). See spec acceptance for the trigger list.
_best_laps_lock = threading.RLock()
_best_laps_cache: dict[tuple[str, str, str, str], dict[str, int]] | None = None


# Fold→display driver-name lookup. Cached at module level so the per-tick
# WS publish path doesn't hit Mongo every time. Refreshed lazily on cache
# miss inside `_get_driver_name_lookup` and force-refreshed by the
# best-laps refresh (the natural "something material changed" signal —
# also the moment the assembly layer rebuilds full snapshots).
#
# Missing-key warning suppression: the assembly layer logs once per
# unknown folded key so a noisy AC session doesn't spam the log.
_driver_lookup_lock = threading.RLock()
_driver_name_lookup: dict[str, str] | None = None
_logged_missing_driver_keys: set[str] = set()


# Active-stream tracking. The WS broadcaster sends an `active_state`
# envelope on connect AND on every transition. `_active_state` is the
# last-known canonical state; comparisons happen on the consumer thread
# after `_record_message` or a stale sweep updates it.
#
# Stale detection uses `2 * STALE_AFTER_S` per spec §8 ("Stream flicker")
# to give the toggle a 20 s hysteresis vs. the active-row 10 s window.
ACTIVE_STATE_STALE_AFTER_S = STALE_AFTER_S * 2
_active_state_lock = threading.RLock()
_active_state: dict[str, Any] = {
    "is_active": False,
    "driver": None,
    "track": None,
    "car": None,
    "experiment": None,
    "environment": None,
}


# ---------------------------------------------------------------------------
# Simulator (LOCAL_DEV_MODE)
# ---------------------------------------------------------------------------

# Sim is module-level state so successive requests see the same lap start.
_SIM_LAP_TARGET_MS = 91_000
_SIM_DRIVER = "Ludvík"
_SIM_CAR = "bmw_1m"
_SIM_TRACK = "ks_nurburgring"
_SIM_EXPERIMENT = "baseline"

_sim_lock = threading.RLock()
_sim_start_epoch: float | None = None
_sim_completed_lap_times_ms: list[int] = []
# Sim's gate-crossing state. Mirrors the real-mode per-key state.
_sim_gate_times_ms: list[int | None] = [None] * GATE_COUNT
_sim_last_norm_pos: float = 0.0
_sim_last_lap_index: int = 0


def _sim_jitter_for_lap(lap_index: int) -> int:
    """Per-lap jitter so successive laps differ. +/-300 ms by parity, plus a
    tiny ramp so the delta drifts across many laps."""
    base = 300 if lap_index % 2 == 0 else -300
    return base + (lap_index % 5) * 60


def simulated_active_driver() -> dict[str, Any]:
    """Return a deterministic `LiveDriverState`-shaped dict.

    Rolls the lap when `elapsed >= lap_target_ms`. The first call seeds
    `_sim_start_epoch` at the current wall-clock so the running time starts
    at zero from the perspective of the first request.

    Also maintains `_sim_gate_times_ms` so the gate-breakdown table has
    populated "completed" rows. The crossings are evaluated on every call —
    provided clients poll at 500 ms, every 5% gate is observed inside the
    same lap (the sim's lap is ~91 s long, so each 4.55 s gate spacing
    contains ~9 polls).
    """
    global _sim_start_epoch, _sim_last_norm_pos, _sim_last_lap_index
    global _sim_gate_times_ms

    with _sim_lock:
        now = time.time()
        if _sim_start_epoch is None:
            _sim_start_epoch = now

        # Roll laps as needed. This handles arbitrarily-long gaps between
        # requests (e.g. backend was idle while no client polled).
        while True:
            lap_index = len(_sim_completed_lap_times_ms)
            lap_target = _SIM_LAP_TARGET_MS + _sim_jitter_for_lap(lap_index)
            elapsed_ms = int((now - _sim_start_epoch) * 1000)
            if elapsed_ms < lap_target:
                break
            # Lap completed — record and roll start to keep the leftover
            # into the next lap.
            _sim_completed_lap_times_ms.append(lap_target)
            _sim_start_epoch += lap_target / 1000.0

        # current_lap is 1-indexed in AC's iCurrentTime semantics.
        current_lap = len(_sim_completed_lap_times_ms) + 1
        norm_pos = min(0.9999, max(0.0, elapsed_ms / lap_target))
        best_so_far = (
            min(_sim_completed_lap_times_ms) if _sim_completed_lap_times_ms else None
        )

        # Gate-time bookkeeping. Reset on lap rollover, then stamp any
        # gates crossed since the previous call.
        completed = len(_sim_completed_lap_times_ms)
        if completed != _sim_last_lap_index:
            _sim_gate_times_ms = [None] * GATE_COUNT
            _sim_last_norm_pos = 0.0
            _sim_last_lap_index = completed
        _update_gate_times(_sim_gate_times_ms, _sim_last_norm_pos, norm_pos, elapsed_ms)
        _sim_last_norm_pos = norm_pos

        return {
            "driver": _SIM_DRIVER,
            "car": _SIM_CAR,
            "track": _SIM_TRACK,
            "experiment": _SIM_EXPERIMENT,
            "current_lap": current_lap,
            "current_lap_time_ms": elapsed_ms,
            "normalized_position": norm_pos,
            "best_lap_ms_session": best_so_far,
            "gate_times_ms": list(_sim_gate_times_ms),
            "last_normalized_position": _sim_last_norm_pos,
        }


# ---------------------------------------------------------------------------
# Driver-name display-case lookup helpers (Bug A fix — spec §5.6)
# ---------------------------------------------------------------------------


def _fold_for_lookup(name: str) -> str:
    """Fold a driver name to the same NFKD + lowercase ASCII key the lake
    uses. Local helper so `live_telemetry` doesn't import from
    `routes.leaderboard_real` (which already imports us — circular).
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


def _get_driver_name_lookup() -> dict[str, str]:
    """Return the cached `{folded_key: display_name}` lookup.

    Lazy-built on first call by querying Mongo via the singleton handle.
    Re-queried after every successful `refresh_best_laps_cache` call (see
    `_invalidate_driver_name_lookup`). Returns an empty dict on any
    error — the caller falls back to title-cased folded keys.
    """
    with _driver_lookup_lock:
        if _driver_name_lookup is not None:
            return _driver_name_lookup
    return _refresh_driver_name_lookup()


def _refresh_driver_name_lookup() -> dict[str, str]:
    """(Re)build the fold→display map from Mongo `drivers.name`.

    Returns the new map and stores it in `_driver_name_lookup`. Errors
    leave the previous map (or `None`) in place and return an empty dict
    so callers can keep going.
    """
    try:
        from . import mongo as mongo_mod

        db = mongo_mod.get_mongo()
        lookup: dict[str, str] = {}
        for doc in db.drivers.find({}, {"name": 1}):
            name = doc.get("name")
            if isinstance(name, str) and name:
                lookup[_fold_for_lookup(name)] = name
        with _driver_lookup_lock:
            global _driver_name_lookup
            _driver_name_lookup = lookup
            # Reset missing-key warnings so the next pass through an
            # unknown driver re-logs (rare, helpful when QA is adding
            # drivers to Mongo).
            _logged_missing_driver_keys.clear()
        return lookup
    except Exception:
        logger.exception("driver-name lookup refresh failed; using empty lookup")
        return {}


def _invalidate_driver_name_lookup() -> None:
    """Drop the cached driver-name lookup so the next read repopulates.

    Called after best-laps cache refresh so a freshly-added driver in
    Mongo flows to the wire envelope without waiting for a process
    restart.
    """
    with _driver_lookup_lock:
        global _driver_name_lookup
        _driver_name_lookup = None


def _resolve_display_name(folded_key: str, lookup: dict[str, str]) -> str:
    """Map a folded driver key to Mongo display case.

    Falls back to a title-cased folded key when the lookup misses. Logs a
    WARNING once per unknown key so repeated misses don't spam the log.
    """
    if not folded_key:
        return ""
    display = lookup.get(folded_key)
    if display:
        return display
    with _driver_lookup_lock:
        if folded_key not in _logged_missing_driver_keys:
            _logged_missing_driver_keys.add(folded_key)
            logger.warning(
                "driver-name lookup miss for folded_key=%r; "
                "wire will carry title-cased fallback",
                folded_key,
            )
    return folded_key[:1].upper() + folded_key[1:]


def _lookup_gate_vectors_group(
    track: str, car: str, experiment: str
) -> "dict[str, _HistoricalEntry] | None":
    """Return the historicals for one (track, car, experiment) group, or
    `None` when the gate-vectors cache is cold or the group is unknown.
    """
    with _gate_vectors_lock:
        if _gate_vectors_cache is None:
            return None
        group = _gate_vectors_cache.get((track, car, experiment))
        if group is None:
            return None
        # Defensive copy of the dict view so the caller can iterate
        # without holding the lock; entries themselves are frozen.
        return dict(group)


# ---------------------------------------------------------------------------
# Active-state envelope publisher (spec §5.1)
# ---------------------------------------------------------------------------


def _update_active_state(
    is_active: bool,
    driver: str | None,
    track: str | None,
    car: str | None,
    experiment: str | None,
    environment: str | None,
) -> None:
    """Update the module-level active-state record and broadcast a transition
    envelope when something material changed.

    Material changes:
      * idle → active
      * active → idle
      * combo change while active (any of driver/track/car/experiment)

    A no-op call (same values as before) skips the broadcast — the wire
    only carries transitions, not every tick.
    """
    new_state = {
        "is_active": bool(is_active),
        "driver": driver if is_active else None,
        "track": track if is_active else None,
        "car": car if is_active else None,
        "experiment": experiment if is_active else None,
        "environment": environment if is_active else None,
    }
    with _active_state_lock:
        prev = dict(_active_state)
        _active_state.update(new_state)
        if prev == new_state:
            return
        envelope = _build_active_state_envelope(new_state)
    # Publish outside the lock — the broadcaster handoff is async.
    from . import live_stream

    live_stream.publish_active_state(envelope)


def _build_active_state_envelope(state: dict[str, Any]) -> dict[str, Any]:
    """Project the internal state dict to the wire envelope shape.

    Wire shape (spec §7.1):
        {"type": "active_state", "is_active": bool, "driver": str|null,
         "track": str|null, "car": str|null, "experiment": str|null,
         "environment": str|null}
    """
    return {
        "type": "active_state",
        "is_active": state["is_active"],
        "driver": state["driver"],
        "track": state["track"],
        "car": state["car"],
        "experiment": state["experiment"],
        "environment": state["environment"],
    }


def current_active_state_envelope() -> dict[str, Any]:
    """Return the wire envelope for the current active-stream state.

    Used by the WS endpoint to send the very first `active_state` frame
    immediately after the initial snapshot, so clients reconnecting mid-
    session don't have to wait for a transition.
    """
    with _active_state_lock:
        return _build_active_state_envelope(dict(_active_state))


def sweep_stale_active_state() -> None:
    """Demote the active state to idle when every state entry has gone
    stale (> ACTIVE_STATE_STALE_AFTER_S since last tick).

    Called from the FastAPI broadcaster's keepalive loop. We can't rely
    on a Kafka tick to flip the state to idle because by definition the
    consumer stops receiving ticks when AC goes idle.
    """
    now = time.time()
    with _state_lock:
        any_fresh = any(
            now - e["last_seen_epoch"] < ACTIVE_STATE_STALE_AFTER_S
            for e in _state.values()
        )
    if any_fresh:
        return
    _update_active_state(
        is_active=False,
        driver=None,
        track=None,
        car=None,
        experiment=None,
        environment=None,
    )


# ---------------------------------------------------------------------------
# Live-mode state access
# ---------------------------------------------------------------------------


def _record_message(payload: dict[str, Any]) -> None:
    """Merge one raw Kafka payload into the per-(track, car, driver) state
    dict and publish the resulting WS active-row mutation.

    Bugs A + B (spec §5.6 + §5.3) are fixed here:

    * **Bug A — display name on the wire.** The lake-folded `driver` key
      (e.g. `"tomas"`) is the internal cache key; the WS envelope MUST
      carry the Mongo display case (`"Tomás"`). We consult
      `_get_driver_name_lookup()` (cached, refreshed on best-laps
      refresh) before publishing.
    * **Bug B — per-tick gate-state recompute.** When a new gate is
      crossed during this call, look up the historical gate vectors in
      `_gate_vectors_cache` for the active driver's
      `(track, car, experiment)` and recompute the sticky
      `last_gate_index` / `last_gate_state` / `last_gate_delta_ms` triple
      via the shared `gate_math.compute_last_gate_state`. The
      per-historical inline deltas (spec §7.2) are computed at the same
      time and ride along on the WS envelope.

    On lap rollover (`completedLaps` increment OR `iCurrentTime` reset),
    `gate_times_ms` resets to `[None] * GATE_COUNT`, the previous position
    is rebased to 0, AND the sticky `last_gate_*` fields are cleared back
    to `None` so the previous lap's colour doesn't bleed into the new lap
    until the first gate crossing of the new lap (§8.7).
    """
    track = payload.get("track")
    car = payload.get("carModel") or payload.get("car")
    driver = payload.get("driver")
    if not (track and car and driver):
        return
    try:
        i_current = int(payload.get("iCurrentTime") or 0)
        completed = int(payload.get("completedLaps") or 0)
        norm_pos = float(payload.get("normalizedCarPosition") or 0.0)
        entry_partial: dict[str, Any] = {
            "last_seen_epoch": time.time(),
            "experiment": str(payload.get("experiment") or ""),
            "environment": str(payload.get("environment") or ""),
            "iCurrentTime": i_current,
            "completedLaps": completed,
            "normalizedCarPosition": norm_pos,
            "iLastTime": int(payload.get("iLastTime") or 0),
            "iBestTime": int(payload.get("iBestTime") or 0),
        }
    except (TypeError, ValueError):
        logger.debug("skipping malformed live-telemetry message: %r", payload)
        return

    key = (track, car, driver)
    experiment = entry_partial["experiment"]
    environment = entry_partial["environment"]

    # Snapshot what we need OUTSIDE the state lock down below. Gate-vector
    # lookup is a single dict.get; cheap enough to do inline once we know
    # the (track, car, experiment) the new crossing was scored under.
    historicals_for_group = _lookup_gate_vectors_group(track, car, experiment)

    with _state_lock:
        prev = _state.get(key)
        # Lap rollover detection: spec §8.7 mandates clearing `last_gate_*`
        # when `completedLaps` increments OR `iCurrentTime` resets. The
        # iCurrentTime check guards against AC's mid-session-restart case
        # where the lap counter stays at 0 but the lap clock falls back to
        # 0 ms.
        prev_i_current = int(prev.get("iCurrentTime") or 0) if prev else 0
        rollover = (
            prev is None
            or prev.get("completedLaps", -1) != completed
            or i_current < prev_i_current
        )
        if rollover or prev is None:
            gate_times: list[int | None] = [None] * GATE_COUNT
            prev_pos = 0.0
            last_gate_index: int | None = None
            last_gate_state: str | None = None
            last_gate_delta_ms: int | None = None
        else:
            gate_times = list(prev.get("gate_times_ms") or [None] * GATE_COUNT)
            if len(gate_times) != GATE_COUNT:
                gate_times = [None] * GATE_COUNT
            prev_pos = float(prev.get("normalizedCarPosition") or 0.0)
            last_gate_index = prev.get("last_gate_index")
            last_gate_state = prev.get("last_gate_state")
            last_gate_delta_ms = prev.get("last_gate_delta_ms")

        newly_crossed = _update_gate_times(gate_times, prev_pos, norm_pos, i_current)

        # Bug B: if any gate just flipped None → ms during this tick,
        # recompute the sticky triple immediately (before publish) using
        # the same `gate_math` helper the snapshot-rebuild path uses.
        if newly_crossed:
            # Lazy import to avoid the module-level cycle:
            # live_telemetry → gate_math is one-way.
            from . import gate_math

            new_i, new_state, new_delta = gate_math.compute_last_gate_state(
                gate_times, historicals_for_group, GATE_COUNT
            )
            last_gate_index = new_i if new_i is not None else last_gate_index
            last_gate_state = new_state if new_state is not None else last_gate_state
            if new_delta is not None:
                last_gate_delta_ms = new_delta

        entry_partial["gate_times_ms"] = gate_times
        entry_partial["last_gate_index"] = last_gate_index
        entry_partial["last_gate_state"] = last_gate_state
        entry_partial["last_gate_delta_ms"] = last_gate_delta_ms
        _state[key] = entry_partial

    # Compute the per-historical inline deltas the active envelope carries
    # on the wire (spec §7.2). Folded → display name mapping happens here
    # so the frontend's exact-equality match (`row.driver === delta.driver`)
    # works without any client-side folding.
    #
    # Bandwidth optimisation: only ship the deltas on ticks that produced
    # a new gate crossing. Sticky values between crossings are already on
    # the frontend's historical rows from the previous broadcast; an
    # empty `historical_deltas` dict skips the per-row patch entirely
    # (see `patchActiveRow` in `use-live-stream.ts`). ~80 B per gate
    # crossing × ≤20 gates per lap = trivial.
    name_lookup = _get_driver_name_lookup()
    if newly_crossed:
        from . import gate_math

        folded_deltas = gate_math.compute_per_historical_deltas(
            gate_times, historicals_for_group, GATE_COUNT
        )
        historical_deltas = {
            _resolve_display_name(folded, name_lookup): delta_ms
            for folded, delta_ms in folded_deltas.items()
        }
    else:
        historical_deltas = {}

    # Bug A: resolve the active-row driver name to display case BEFORE
    # publish so snapshots and active mutations carry identical text.
    display_driver = _resolve_display_name(_fold_for_lookup(driver), name_lookup)

    snapshot = {
        "driver": display_driver,
        "car": car,
        "track": track,
        "experiment": experiment,
        "current_lap": (entry_partial["completedLaps"] or 0) + 1,
        "current_lap_time_ms": entry_partial["iCurrentTime"],
        "normalized_position": entry_partial["normalizedCarPosition"],
        "last_gate_index": entry_partial["last_gate_index"],
        "last_gate_state": entry_partial["last_gate_state"],
        "last_gate_delta_ms": entry_partial["last_gate_delta_ms"],
        "historical_deltas": historical_deltas,
    }
    # Update the canonical active-state record + publish a transition
    # envelope if anything material changed.
    _update_active_state(
        is_active=True,
        driver=display_driver,
        track=track,
        car=car,
        experiment=experiment,
        environment=environment,
    )

    # Notify the WebSocket broadcaster outside the state lock so a slow
    # event-loop scheduler can't backpressure the Kafka consumer.
    # `publish_snapshot` is a no-op when the broadcaster isn't running
    # (e.g. pytest, startup race) and swallows every failure — the
    # consumer loop must never crash on a missing event loop.
    from . import live_stream

    live_stream.publish_snapshot(snapshot)


def get_active_driver() -> dict[str, Any] | None:
    """Return the freshest non-stale entry as a `LiveDriverState`-shaped
    dict, or None if nothing has been seen in the last `STALE_AFTER_S`.

    Includes the sticky `last_gate_index` / `last_gate_state` /
    `last_gate_delta_ms` triple so the assembly layer can stamp them onto
    the active row even between crossings (server-side stickiness).
    """
    now = time.time()
    with _state_lock:
        candidates = [
            (key, entry)
            for key, entry in _state.items()
            if now - entry["last_seen_epoch"] < STALE_AFTER_S
        ]
    if not candidates:
        return None
    # Freshest wins. V1 ignores experiment in the key — we want broad active
    # detection across config swaps mid-test.
    key, entry = max(candidates, key=lambda kv: kv[1]["last_seen_epoch"])
    track, car, driver = key
    best = entry["iBestTime"] or None
    gate_times = entry.get("gate_times_ms") or [None] * GATE_COUNT
    return {
        "driver": driver,
        "car": car,
        "track": track,
        "experiment": entry["experiment"],
        "current_lap": (entry["completedLaps"] or 0) + 1,
        "current_lap_time_ms": entry["iCurrentTime"],
        "normalized_position": entry["normalizedCarPosition"],
        "best_lap_ms_session": best,
        "gate_times_ms": list(gate_times),
        "last_normalized_position": float(entry["normalizedCarPosition"]),
        "last_gate_index": entry.get("last_gate_index"),
        "last_gate_state": entry.get("last_gate_state"),
        "last_gate_delta_ms": entry.get("last_gate_delta_ms"),
    }


# ---------------------------------------------------------------------------
# Enrichment helpers (session cache + DCM experiment lookup)
# ---------------------------------------------------------------------------


def _dcm_auth_headers(sdk_token: str | None) -> dict[str, str]:
    """Build the `Authorization` header used for all DCM HTTP calls.

    Matches the pattern in `api/app.py:_probe_config_api` (and used by every
    DCM call in this module): `Authorization: Bearer <token>` when a token
    is configured, empty dict otherwise. Centralised so the prewarm and the
    experiment lookup stay in lockstep.
    """
    return {"Authorization": f"Bearer {sdk_token}"} if sdk_token else {}


def _dcm_get_content(
    content_url: str, headers: dict[str, str]
) -> dict[str, Any] | None:
    """GET a DCM content URL and return the parsed JSON dict.

    Used by `_handle_config_event` — the DCM events topic already provides
    a fully-qualified `contentUrl` for the changed version, so the caller
    can skip the list-configurations / pick-latest-version dance that
    `_fetch_latest_version_content` does. Returns `None` on any error
    (non-200, malformed JSON, non-dict body); errors are logged at
    WARNING and swallowed so a single bad event can't kill the consumer.
    """
    import httpx

    try:
        with httpx.Client(timeout=DCM_TIMEOUT_S) as client:
            resp = client.get(content_url, headers=headers)
        if resp.status_code != 200:
            logger.warning(
                "DCM content fetch returned %d for url=%s",
                resp.status_code,
                content_url,
            )
            return None
        body = resp.json()
        if not isinstance(body, dict):
            return None
        return body
    except Exception:
        logger.exception("DCM content fetch failed for url=%s", content_url)
        return None


def _fetch_latest_version_content(
    client: Any,
    base: str,
    config_id: str,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    """Fetch the content of the highest-numbered version of a DCM config.

    Returns the content dict on success, or `None` if either DCM call fails
    or there are no versions. Errors are logged at WARNING and swallowed —
    callers are expected to treat `None` as "no data" rather than propagate.

    Used by both the experiment lookup (single hostname on session change)
    and the session prewarm (all session configs at consumer startup), so
    the version-pick logic stays in one place. Mirrors
    `session-config-bridge/main.py:_get_current_test_id` lines 130-158.
    """
    v_resp = client.get(f"{base}/configurations/{config_id}/versions", headers=headers)
    if v_resp.status_code != 200:
        logger.warning(
            "DCM versions returned %d for config=%s",
            v_resp.status_code,
            config_id,
        )
        return None
    versions = v_resp.json()
    if isinstance(versions, dict):
        versions = versions.get("data", versions.get("items", []))
    if not versions:
        return None
    latest = max(
        versions,
        key=lambda v: v.get("metadata", v).get("version") or 0,
    )
    version = latest.get("metadata", latest).get("version")

    c_resp = client.get(
        f"{base}/configurations/{config_id}/versions/{version}/content",
        headers=headers,
    )
    if c_resp.status_code != 200:
        logger.warning(
            "DCM content fetch returned %d for config=%s v%s",
            c_resp.status_code,
            config_id,
            version,
        )
        return None
    content = c_resp.json() or {}
    if not isinstance(content, dict):
        return None
    return content


def _fetch_experiment_from_dcm(hostname: str) -> dict[str, str]:
    """Resolve the active experiment + driver + environment for a hostname via the DCM HTTP API.

    Returns a dict `{"experiment_id": str, "driver": str, "environment": str}`.
    All three default to `""` when no experiment config exists for this
    hostname or any DCM call fails. `experiment_id` matches the Hive
    `experiment` partition in the lake (DCM content stores it under the
    legacy key `experiment_id`); `driver` is the canonical "who is driving
    this test" set by Test Manager (`api/routes/tests.py:sync_to_dcm` writes
    `content.driver = test.driver.lower()` alongside `experiment_id`);
    `environment` matches the Hive `environment` partition (`tests.py`
    line 89: `content.environment = partition["environment"]`). Sourcing all
    three from the same single DCM call keeps the network cost identical to
    the prior experiment-only fetch.

    Network-blocking but called only on session-message arrival (rare event,
    not per-tick). All exceptions are caught here — the consumer loop must
    never crash because DCM is briefly unavailable. A miss caches empty
    strings so we don't hammer DCM for hosts that genuinely have no config.

    Adapted from `session-config-bridge/main.py:107-161`; we don't import
    from that service per the spec.
    """
    # Imported lazily so the simulator and tests that don't run the consumer
    # don't pay the import cost.
    import httpx

    from .settings import get_settings

    empty = {"experiment_id": "", "driver": "", "environment": ""}

    settings = get_settings()
    base = f"{settings.config_api_url.rstrip('/')}/api/v1"
    headers = _dcm_auth_headers(settings.sdk_token)

    try:
        with httpx.Client(timeout=DCM_TIMEOUT_S) as client:
            # 1. Find the experiment config_id for this hostname.
            resp = client.get(f"{base}/configurations", headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "DCM list returned %d when resolving experiment for %s",
                    resp.status_code,
                    hostname,
                )
                return dict(empty)
            data = resp.json()
            configs = (
                data
                if isinstance(data, list)
                else data.get("data", data.get("items", []))
            )
            config_id: str | None = None
            for cfg in configs:
                meta = cfg.get("metadata") or {}
                if (
                    meta.get("type") == "experiment"
                    and meta.get("target_key") == hostname
                ):
                    config_id = cfg.get("id") or cfg.get("_id")
                    break
            if not config_id:
                logger.info("No experiment config in DCM for hostname=%s", hostname)
                return dict(empty)

            # 2. Pick the latest version and fetch its content.
            content = _fetch_latest_version_content(client, base, config_id, headers)
            if content is None:
                return dict(empty)

            # Content keys: `experiment_id` (legacy name; same value the lake
            # uses as the `experiment` Hive partition), `driver` (set by
            # `api/routes/tests.py:sync_to_dcm`, also the lake's `driver`
            # partition), and `environment` (the lake's `environment`
            # Hive partition). See `tests.py` lines 89-92.
            experiment = str(content.get("experiment_id") or "")
            driver = str(content.get("driver") or "")
            environment = str(content.get("environment") or "")
            logger.info(
                "DCM lookup OK: hostname=%s config=%s experiment=%r driver=%r env=%r",
                hostname,
                config_id,
                experiment,
                driver,
                environment,
            )
            return {
                "experiment_id": experiment,
                "driver": driver,
                "environment": environment,
            }
    except Exception:
        # Catch broad on purpose — httpx errors, JSON decode errors, etc.
        # Caller treats empty strings as "unknown experiment/driver".
        logger.exception("DCM experiment lookup failed for hostname=%s", hostname)
        return dict(empty)


def _prewarm_session_cache_from_dcm() -> None:
    """Pre-populate `_session_cache` (and `_experiment_cache`) from DCM at
    consumer startup so a backend restart mid-AC-session immediately has the
    enrichment data it would otherwise only learn on the next session
    message.

    Steps (best-effort — any failure logs once and returns without
    blocking the consumer loop):

      1. List configurations from DCM.
      2. Filter to `metadata.type == "session"` AND
         `metadata.category == "ac-telemetry"`.
      3. For each match, pull the latest version's content and seed
         `_session_cache[hostname]` with track/carModel/playerName.
      4. Force-refresh `_get_cached_experiment(hostname)` so the experiment
         cache is hot too — same hook the session-message handler uses.
         That single call populates BOTH the experiment_id and the DCM
         driver fields of the cache entry; no additional HTTP traffic.

    Why DCM and not Kafka with `auto_offset_reset="earliest"`: session-topic
    retention may include long-dead hostnames; DCM stores exactly one
    "current session" per hostname, which is what we want.

    Final step: a one-shot `_refresh_best_laps_from_settings()` so the
    cached per-driver best laps reflect the drivers we just learned
    about. The extra lake hit (on top of the consumer's existing startup
    warm-up) is bounded — one extra call per backend boot, cache swap is
    atomic.
    """
    try:
        import httpx

        from .settings import get_settings

        settings = get_settings()
        config_api_url = settings.config_api_url
        if not config_api_url:
            logger.debug("skipping DCM session pre-warm: config_api_url not configured")
            return

        base = f"{config_api_url.rstrip('/')}/api/v1"
        headers = _dcm_auth_headers(settings.sdk_token)

        with httpx.Client(timeout=DCM_TIMEOUT_S) as client:
            resp = client.get(f"{base}/configurations", headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "DCM list returned %d during session pre-warm", resp.status_code
                )
                return
            data = resp.json()
            configs = (
                data
                if isinstance(data, list)
                else data.get("data", data.get("items", []))
            )

            session_configs = [
                cfg
                for cfg in configs
                if (
                    (cfg.get("metadata") or {}).get("type") == "session"
                    and (cfg.get("metadata") or {}).get("category") == "ac-telemetry"
                )
            ]
            if not session_configs:
                logger.debug("no DCM session configs to prewarm")
                return

            prewarmed_hostnames: list[str] = []
            for cfg in session_configs:
                meta = cfg.get("metadata") or {}
                hostname = str(meta.get("target_key") or "").strip()
                config_id = cfg.get("id") or cfg.get("_id")
                if not hostname or not config_id:
                    continue

                content = _fetch_latest_version_content(
                    client, base, config_id, headers
                )
                if not content:
                    continue

                track = str(content.get("track") or "").strip()
                car = str(content.get("carModel") or "").strip()
                player = str(content.get("playerName") or "").strip()
                if not (track and car):
                    logger.debug(
                        "skipping DCM session prewarm for hostname=%s: "
                        "missing track/carModel in content",
                        hostname,
                    )
                    continue

                with _state_lock:
                    _session_cache[hostname] = {
                        "track": track,
                        "carModel": car,
                        "playerName": player,
                    }
                logger.info(
                    "session cache prewarmed from DCM: hostname=%s track=%s car=%s",
                    hostname,
                    track,
                    car,
                )
                prewarmed_hostnames.append(hostname)

        # Warm the experiment cache for each hostname we seeded. Outside the
        # httpx context so each lookup opens its own short-lived client —
        # consistent with `_get_cached_experiment`'s existing behaviour.
        for hostname in prewarmed_hostnames:
            _get_cached_experiment(hostname, force_refresh=True)

        # Refresh best-laps once more so per-driver bests reflect the
        # drivers we just discovered. Cheap, atomic, one extra lake hit at
        # boot. (Step 2 will extend this to gate vectors.)
        if prewarmed_hostnames:
            _refresh_best_laps_from_settings()
    except Exception:
        logger.exception("DCM session pre-warm failed; continuing without it")


def _get_cached_experiment(hostname: str, force_refresh: bool = False) -> str:
    """Return the cached experiment_id for a hostname, refreshing on TTL
    expiry or `force_refresh=True` (used when a new session message arrives).

    A single DCM call populates BOTH the `experiment` and `driver` fields of
    the cache entry; callers that need the driver should read it via
    `_get_cached_driver(hostname)` (no extra HTTP call).

    The DCM HTTP call is performed under the same `_state_lock` as the
    session cache. That's coarse — but only the session-message handler
    enters this path, which is rare (once per AC session change). The hot
    per-tick path never calls this function.
    """
    now = time.time()
    with _state_lock:
        entry = _experiment_cache.get(hostname)
        if (
            not force_refresh
            and entry is not None
            and now - entry["fetched_epoch"] < EXPERIMENT_CACHE_TTL_S
        ):
            return str(entry["experiment"])

    # Fetch outside the lock — `httpx` can take seconds on a slow DCM and we
    # don't want raw-tick recording to block on it.
    config = _fetch_experiment_from_dcm(hostname)
    experiment = str(config.get("experiment_id") or "")
    driver = str(config.get("driver") or "")
    environment = str(config.get("environment") or "")
    with _state_lock:
        _experiment_cache[hostname] = {
            "experiment": experiment,
            "driver": driver,
            "environment": environment,
            "fetched_epoch": time.time(),
        }
    return experiment


def _get_cached_driver(hostname: str) -> str:
    """Return the cached DCM driver for a hostname (or `""` on miss).

    Read-only sibling of `_get_cached_experiment` — never triggers a DCM
    fetch on its own. The expectation is that `_get_cached_experiment(...)`
    has already populated the entry (it's called force_refresh on every
    session-message arrival and at consumer startup via the DCM prewarm),
    so the hot per-tick path can read `driver` here without ever blocking
    on the network. A genuine miss (no session config yet for this host)
    returns `""`, signalling the caller to fall back to `playerName`.
    """
    with _state_lock:
        entry = _experiment_cache.get(hostname)
        if entry is None:
            return ""
        return str(entry.get("driver") or "")


def _get_cached_environment(hostname: str) -> str:
    """Return the cached DCM environment for a hostname (or `""` on miss).

    Same read-only semantics as `_get_cached_driver`. Populated as a
    side-effect of `_get_cached_experiment(...)` — there is no separate
    DCM fetch path. The best-laps refresh enumerates hostnames it knows
    about (via `_session_cache`) and reads their `environment` value here
    to build the per-(track, car, exp, env) lake query set.
    """
    with _state_lock:
        entry = _experiment_cache.get(hostname)
        if entry is None:
            return ""
        return str(entry.get("environment") or "")


def _handle_session_message(hostname: str, payload: dict[str, Any]) -> None:
    """Cache static session metadata for a hostname and refresh the DCM
    experiment cache. One AC session change → one DCM HTTP call.
    """
    track = str(payload.get("track") or "").strip()
    car = str(payload.get("carModel") or "").strip()
    player = str(payload.get("playerName") or "").strip()
    if not (track and car):
        logger.debug(
            "ignoring session message with missing track/carModel for hostname=%s",
            hostname,
        )
        return
    with _state_lock:
        _session_cache[hostname] = {
            "track": track,
            "carModel": car,
            "playerName": player,
        }
    logger.info(
        "session cache updated: hostname=%s track=%s car=%s driver=%s",
        hostname,
        track,
        car,
        player,
    )
    # Force-refresh the experiment cache on every session change. AC session
    # boundaries are the natural invalidation point — between sessions the
    # operator may have switched the active experiment via Test Manager.
    _get_cached_experiment(hostname, force_refresh=True)
    # Same trigger for the best-laps cache — a new AC session means a
    # driver is about to set new lap times, so we want fresh per-driver
    # best laps in cache before the next `/live-positions` poll.
    # Synchronous is fine: this handler runs on the consumer thread, off
    # the HTTP request path, so the seconds-long lake call doesn't hurt
    # API users. Canonical "once per AC session" refresh trigger.
    _refresh_best_laps_from_settings()


def _handle_config_event(payload: dict[str, Any]) -> None:
    """Apply one `ac-telemetry-config` event to the per-hostname caches.

    DCM publishes to this topic whenever a session or experiment config is
    created, updated, or deleted. Reacting here closes the latency gap
    between "user edits a test in Test Manager" and "live leaderboard
    starts crediting laps to the new driver" — previously the cache only
    refreshed on the next AC session message.

    Validation: the event must carry `metadata.category == "ac-telemetry"`,
    `metadata.type in {"session", "experiment"}`, and a non-empty
    `metadata.target_key` (hostname). Anything else is debug-logged and
    ignored. Errors throughout are caught — a single malformed event must
    not kill the consumer loop.

    Behaviour by event type:
      * `"deleted"` → drop the matching cache entry; no HTTP call.
      * `"created"` / `"updated"` (or any non-delete value) → GET
        `contentUrl` (already absolute) and update the cache. For
        `type == "session"` we also force-refresh the experiment cache so
        experiment + driver stay paired (mirrors `_handle_session_message`).

    Historicals are refreshed only on session-type events — experiment
    metadata changes don't add laps, so the historicals cache stays
    valid. Track / carModel / playerName come straight from DCM here, so
    the source-of-truth fallback (AC's `ac-telemetry-session`) is
    unaffected — both paths converge on the same `_session_cache` shape.
    """
    try:
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            logger.debug("config event has non-dict metadata: %r", payload)
            return
        category = metadata.get("category")
        event_type = metadata.get("type")
        target_key = str(metadata.get("target_key") or "").strip()
        event = str(payload.get("event") or "").strip().lower()

        if (
            category != "ac-telemetry"
            or event_type not in ("session", "experiment")
            or not target_key
        ):
            logger.debug(
                "ignoring config event: category=%r type=%r target_key=%r",
                category,
                event_type,
                target_key,
            )
            return

        # Deletion path: drop the cache entry and return. No HTTP needed —
        # the content URL would 404 anyway.
        if event == "deleted":
            with _state_lock:
                if event_type == "session":
                    _session_cache.pop(target_key, None)
                else:
                    _experiment_cache.pop(target_key, None)
            logger.info(
                "config event applied: type=%s hostname=%s (deleted)",
                event_type,
                target_key,
            )
            return

        # Created / updated / any other non-delete event: fetch content
        # from the URL supplied by DCM. The contentUrl is absolute (e.g.
        # `http://dynamic-configuration-manager/api/v1/...`) so we can
        # use it directly without rebuilding from `Settings.config_api_url`.
        content_url = str(payload.get("contentUrl") or "").strip()
        if not content_url:
            logger.debug(
                "config event has no contentUrl: type=%s hostname=%s event=%s",
                event_type,
                target_key,
                event,
            )
            return

        from .settings import get_settings

        settings = get_settings()
        headers = _dcm_auth_headers(settings.sdk_token)
        content = _dcm_get_content(content_url, headers)
        if not content:
            logger.warning(
                "config event content unavailable: type=%s hostname=%s url=%s",
                event_type,
                target_key,
                content_url,
            )
            return

        if event_type == "session":
            track = str(content.get("track") or "").strip()
            car = str(content.get("carModel") or "").strip()
            player = str(content.get("playerName") or "").strip()
            if not (track and car):
                logger.debug(
                    "config event session content missing track/carModel for %s",
                    target_key,
                )
                return
            with _state_lock:
                _session_cache[target_key] = {
                    "track": track,
                    "carModel": car,
                    "playerName": player,
                }
            logger.info(
                "config event applied: type=session hostname=%s track=%s car=%s",
                target_key,
                track,
                car,
            )
            # Keep experiment+driver paired with the new session (same hook
            # `_handle_session_message` runs after a Kafka session message).
            _get_cached_experiment(target_key, force_refresh=True)
            # And refresh best laps — a session-config change can mean a
            # different driver is about to log laps under this hostname.
            _refresh_best_laps_from_settings()
        else:
            # experiment-type event: update experiment_cache only. We still
            # refresh best-laps below because changing the experiment_id /
            # environment for a hostname changes which lake partition the
            # leaderboard should be summarising.
            experiment = str(content.get("experiment_id") or "")
            driver = str(content.get("driver") or "")
            environment = str(content.get("environment") or "")
            with _state_lock:
                _experiment_cache[target_key] = {
                    "experiment": experiment,
                    "driver": driver,
                    "environment": environment,
                    "fetched_epoch": time.time(),
                }
            logger.info(
                "config event applied: type=experiment hostname=%s "
                "experiment=%r driver=%r env=%r",
                target_key,
                experiment,
                driver,
                environment,
            )
            # Refresh so the new (track, car, experiment, environment)
            # tuple gets queried — see spec acceptance: trigger 3 covers
            # DCM experiment-type events.
            _refresh_best_laps_from_settings()
    except Exception:
        # Broad catch on purpose — handler errors must never break the loop.
        logger.exception("config event handler failed: %r", payload)


def get_best_laps_cache() -> dict[tuple[str, str, str, str], dict[str, int]] | None:
    """Return the current cached best-laps dict, or `None` if no refresh
    has run yet.

    Shape: `{(track, car, experiment, environment): {driver_folded:
    best_lap_ms}}`.

    Callers must treat the returned dict as read-only — we hand back the
    live reference (cheap) rather than a deep copy. The cache is only
    swapped atomically via `refresh_best_laps_cache`, so a caller
    iterating the dict will see a consistent snapshot for the duration of
    that iteration even if a refresh races (the swap replaces the binding,
    not the dict contents in place).
    """
    with _best_laps_lock:
        return _best_laps_cache


def _known_groups() -> list[tuple[str, str, str, str]]:
    """Enumerate every (track, car, experiment, environment) tuple we have
    enrichment data for.

    Sources:
      * `_session_cache[hostname]` → (track, carModel) — populated by AC
        session messages and DCM session-prewarm.
      * `_experiment_cache[hostname]` → (experiment, environment) — populated
        by `_get_cached_experiment` from the matching DCM experiment config.

    A hostname missing either side (e.g. session exists but experiment
    config has no environment yet) is dropped — the lake query can't run
    without all four filters. Each tuple appears at most once even if
    multiple hostnames share the same combo.
    """
    with _state_lock:
        # Snapshot under lock so we don't trip over an in-flight write.
        sessions = dict(_session_cache)
        experiments = dict(_experiment_cache)
    seen: set[tuple[str, str, str, str]] = set()
    groups: list[tuple[str, str, str, str]] = []
    for hostname, session in sessions.items():
        track = str(session.get("track") or "").strip()
        car = str(session.get("carModel") or "").strip()
        exp_entry = experiments.get(hostname)
        if not exp_entry:
            continue
        experiment = str(exp_entry.get("experiment") or "").strip()
        environment = str(exp_entry.get("environment") or "").strip()
        if not (track and car and experiment and environment):
            continue
        key = (track, car, experiment, environment)
        if key in seen:
            continue
        seen.add(key)
        groups.append(key)
    return groups


def refresh_best_laps_cache(
    quixlake_url: str,
    quix_lake_token: str,
) -> None:
    """Query QuixLake once per known (track, car, experiment, environment)
    group and atomically swap the result into `_best_laps_cache`.

    The SQL uses AC's `iBestTime` directly (already a per-driver best in
    ms) and includes the `environment` filter the lake partitions on —
    fixing an earlier bug where missing the environment filter scanned
    too wide a slice while still returning sparse Python-reduced lap times.

    Group discovery: `_known_groups()` enumerates every (track, car,
    experiment, environment) tuple we have enrichment for. We run one
    query per group (typically 1–2 groups in practice).

    Lazy-imports `_query_best_laps` from `routes.leaderboard_real` to
    keep import order one-way (leaderboard_real already imports
    live_telemetry, so the module-level import would cycle).

    All exceptions are caught and logged; on failure the previous cache
    value (possibly None on first run) stays valid.

    After a successful swap, broadcasts a fresh full snapshot through
    `live_stream` so every connected WebSocket client re-renders.
    """
    try:
        from concurrent.futures import ThreadPoolExecutor

        from .routes.leaderboard_real import _query_best_laps

        new_cache: dict[tuple[str, str, str, str], dict[str, int]] = {}
        groups = _known_groups()
        if not groups:
            logger.info(
                "best-laps cache refresh: no (track,car,exp,env) groups known yet "
                "(session + experiment caches still warming) — leaving cache as-is"
            )

        def _run_one(
            grp: tuple[str, str, str, str],
        ) -> tuple[tuple[str, str, str, str], dict[str, int]]:
            track, car, experiment, environment = grp
            per_driver = _query_best_laps(
                quixlake_url,
                quix_lake_token,
                track=track,
                car=car,
                experiment=experiment,
                environment=environment,
            )
            logger.info(
                "best-laps query: track=%s car=%s exp=%s env=%s -> %d drivers",
                track,
                car,
                experiment,
                environment,
                len(per_driver),
            )
            return grp, per_driver

        if groups:
            # Run one lake query per group in parallel. QuixLakeClient is
            # `requests`-based (blocking), so a thread pool is the right
            # primitive — we're already on the Kafka consumer thread, not
            # an event loop. Cap concurrency at 8 to avoid hammering the
            # lake on workspaces with many active drivers.
            with ThreadPoolExecutor(max_workers=min(8, len(groups))) as pool:
                for grp, per_driver in pool.map(_run_one, groups):
                    new_cache[grp] = per_driver
    except Exception:
        logger.exception("best-laps cache refresh failed; keeping previous cache")
        return

    # Only swap when we actually queried something. An empty `new_cache`
    # because no groups were known yet is NOT a "refreshed, lake had no
    # rows" outcome — it's "we don't know what to ask for yet". Leaving
    # the previous cache (or `None`) intact lets `build_live_positions`
    # retry on the next request.
    if not new_cache:
        return

    with _best_laps_lock:
        global _best_laps_cache
        _best_laps_cache = new_cache
    total_historicals = sum(len(g) for g in new_cache.values())
    logger.info(
        "best-laps cache refreshed: %d groups, %d historicals",
        len(new_cache),
        total_historicals,
    )

    # Best-laps changed → drop the driver-name lookup so a newly added
    # driver flows to the wire envelope on the next publish without
    # waiting for a process restart.
    _invalidate_driver_name_lookup()

    # Pair with a gate-vectors refresh: the Left-table colour cue depends
    # on per-gate cumulative-time vectors of every historical's best lap
    # for the same `(track, car, experiment)` set. Same trigger cadence
    # (session / config / startup), one place to keep them in sync.
    refresh_gate_vectors_cache(quixlake_url, quix_lake_token)

    # Historicals changed → broadcast a fresh full snapshot to every WS
    # client so the leaderboard tab updates without waiting for the next
    # connect. Best-effort: any failure inside the broadcast helpers is
    # logged and swallowed so the consumer thread stays alive.
    _broadcast_full_snapshot_safely()


def get_gate_vectors_cache() -> (
    dict[tuple[str, str, str], dict[str, _HistoricalEntry]] | None
):
    """Return the cached `_gate_vectors_cache`, or `None` if no refresh
    has run yet.

    Shape: `{(track, car, experiment): {driver_folded: _HistoricalEntry}}`.

    Callers must treat the returned dict as read-only — we hand back the
    live reference for cheapness. Refreshes swap the reference atomically
    so an iterator never tears across two cache generations.
    """
    with _gate_vectors_lock:
        return _gate_vectors_cache


def refresh_gate_vectors_cache(
    quixlake_url: str,
    quix_lake_token: str,
) -> None:
    """Pull per-gate cumulative-time vectors for every historical best lap
    in scope and atomically swap into `_gate_vectors_cache`.

    Scope = the `(track, car, experiment, environment)` tuples enumerated
    by `_known_groups()`. The reducer in `routes/leaderboard_real.py`
    returns `{(track, car, experiment): {driver_folded: _HistoricalEntry}}`
    keyed without environment because the active-row code path only knows
    (track, car, experiment) — `environment` is a lake-side partition
    that the assembly layer filters on but doesn't echo back to the live
    state. The reducer naturally drops `environment` from its key on
    output.

    Triggered from:
      * consumer startup (after `_prewarm_session_cache_from_dcm`),
      * AC `ac-telemetry-session` Kafka message,
      * DCM `ac-telemetry-config` event (session OR experiment type),
      * (implicitly) every `refresh_best_laps_cache` call, since the two
        caches always refresh together.

    Failures are swallowed + logged; the previous cache stays valid.
    """
    try:
        from .routes.leaderboard_real import (
            _query_gate_samples,
            _reduce_to_gate_vectors,
        )

        groups = _known_groups()
        if not groups:
            logger.info(
                "gate-vectors cache refresh: no groups known yet — leaving cache as-is"
            )
            return

        # The reducer expects a `{(track, car, experiment, driver): (best_ms,
        # lap_num)}` map. The best-laps cache only carries `best_ms` (no
        # lap number). Since `_query_gate_samples` keys on `(track, car,
        # experiment, driver, lap)`, we have to discover the lap number
        # per driver. The cheapest discovery query is a single GROUP BY
        # per group asking "for each driver, which lap matches their
        # `MIN(iBestTime)`?". We piggy-back on `_query_best_laps_with_lap`
        # added below for this purpose.
        from concurrent.futures import ThreadPoolExecutor

        from .routes.leaderboard_real import _query_best_laps_with_lap

        def _run_one_group(
            grp: tuple[str, str, str, str],
        ) -> tuple[tuple[str, str, str, str], dict[str, tuple[int, int]]]:
            track, car, experiment, environment = grp
            per = _query_best_laps_with_lap(
                quixlake_url,
                quix_lake_token,
                track=track,
                car=car,
                experiment=experiment,
                environment=environment,
            )
            return grp, per

        best_per_group: dict[tuple[str, str, str, str], tuple[int, int]] = {}
        with ThreadPoolExecutor(max_workers=min(8, len(groups))) as pool:
            for (track, car, experiment, environment), per_driver_with_lap in pool.map(
                _run_one_group, groups
            ):
                for lake_driver, (best_ms, lap_num) in per_driver_with_lap.items():
                    # Key on the RAW lake driver field so the gate-samples
                    # SQL WHERE clause sees the same string the lake
                    # stores. Folding to the wire/cache form happens at
                    # the tail of the pipeline.
                    best_per_group[(track, car, experiment, lake_driver)] = (
                        int(best_ms),
                        int(lap_num),
                    )

        if not best_per_group:
            reduced: dict[tuple[str, str, str], dict[str, _HistoricalEntry]] = {}
        else:
            sample_rows = _query_gate_samples(
                quixlake_url, quix_lake_token, best_per_group
            )
            reduced = _reduce_to_gate_vectors(best_per_group, sample_rows)
    except Exception:
        logger.exception("gate-vectors cache refresh failed; keeping previous cache")
        return

    with _gate_vectors_lock:
        global _gate_vectors_cache
        _gate_vectors_cache = reduced
    total_historicals = sum(len(g) for g in reduced.values())
    logger.info(
        "gate-vectors cache refreshed: %d (track,car,exp) groups, %d historicals",
        len(reduced),
        total_historicals,
    )


def _broadcast_full_snapshot_safely() -> None:
    """Build and publish a full leaderboard snapshot for all WS clients.

    Called from the Kafka consumer thread after the best-laps cache
    has been swapped. We must:

    * Resolve the Mongo handle the assembly code needs without forcing
      callers to thread it through. `api.mongo.get_mongo()` is a
      module-level singleton populated in the FastAPI lifespan, so it's
      safe to read from a background thread once the app has started.
    * Run `build_live_positions(mongo)` synchronously here — we're
      already on a worker thread (Kafka consumer), not the event loop,
      so the seconds-long Mongo / lake calls don't block any HTTP
      request. `publish_full_snapshot` does the cross-thread handoff
      onto the FastAPI loop.
    * Swallow every exception. A Mongo timeout or a missing-creds
      `LeaderboardError` must not propagate; the next refresh will try
      again.
    """
    try:
        # Lazy imports for the same one-way ordering reason
        # `refresh_best_laps_cache` itself uses: `leaderboard_real`
        # already imports `live_telemetry`, and `live_stream` is a leaf
        # pulled in by `api/app.py` at startup.
        from . import live_stream, mongo
        from .routes.leaderboard_real import build_live_positions

        mongo_db = mongo.get_mongo()
        rows = build_live_positions(mongo_db)
        live_stream.publish_full_snapshot(rows)
    except Exception:
        logger.exception(
            "broadcasting full snapshot after best-laps refresh failed; "
            "WS clients will receive a fresh snapshot on next reconnect"
        )


def _refresh_best_laps_from_settings() -> None:
    """Internal: pull lake creds from `Settings` and refresh the best-laps
    cache.

    Used by the session-message handler, the DCM config-event handler,
    and the consumer-startup warm-up so callers don't have to plumb
    settings through. Silently no-ops when credentials are missing — in
    that mode the route layer raises `LeaderboardError` anyway, so
    populating the cache is moot.
    """
    from .settings import get_settings

    settings = get_settings()
    if not settings.quixlake_url or not settings.quix_lake_token:
        logger.debug(
            "skipping best-laps cache refresh: QuixLake credentials not configured"
        )
        return
    refresh_best_laps_cache(settings.quixlake_url, settings.quix_lake_token)


def _handle_raw_message(hostname: str, payload: dict[str, Any]) -> None:
    """Enrich a raw-topic payload with cached (track, car, driver, experiment)
    and delegate to `_record_message`.

    Cache miss for the hostname means we haven't seen a session message yet
    (e.g. backend started mid-session). We log debug + skip — the next AC
    session will populate the cache. This is intentionally silent at info
    level to avoid spamming.
    """
    with _state_lock:
        session = _session_cache.get(hostname)
        experiment_entry = _experiment_cache.get(hostname)
    if session is None:
        logger.debug("no session cache for hostname=%s; raw tick dropped", hostname)
        return

    enriched = dict(payload)
    enriched["track"] = session["track"]
    enriched["carModel"] = session["carModel"]
    # Driver precedence:
    #   1. an explicit `driver` already on the raw payload (defensive — the
    #      current source doesn't set one, but leave a hook for it),
    #   2. the DCM experiment-config `driver` (canonical "who is driving this
    #      test", same value that drives the lake's `driver` Hive partition),
    #   3. the AC `playerName` from the session topic — fallback for
    #      hostnames that don't yet have a Test Manager experiment config
    #      assigned (e.g. a sim PC running standalone). Keeping this fallback
    #      means a solo lap still produces a leaderboard row instead of being
    #      dropped by `_record_message`'s `(track, car, driver)` guard.
    if not enriched.get("driver"):
        dcm_driver = (
            str(experiment_entry.get("driver") or "") if experiment_entry else ""
        )
        enriched["driver"] = dcm_driver or session["playerName"]
    if not enriched.get("experiment"):
        enriched["experiment"] = (
            str(experiment_entry["experiment"]) if experiment_entry else ""
        )
    _record_message(enriched)


# ---------------------------------------------------------------------------
# Background consumer thread
# ---------------------------------------------------------------------------


def _consumer_loop() -> None:
    """Run QuixStreams' consumer until `_stop_event` is set.

    The thread catches every exception so a bad credential or topic name
    doesn't tear down the FastAPI process. It logs once and exits — the
    HTTP endpoints still serve 204 and the simulator (if enabled) still
    works. Operators get a clear log line to act on.
    """
    try:
        # Imported lazily so test-manager-backend boots fine when
        # quixstreams isn't installed (only required if the consumer runs).
        from quixstreams import Application
    except Exception:
        logger.exception("quixstreams import failed; live consumer disabled")
        return

    try:
        app = Application(
            consumer_group=CONSUMER_GROUP,
            auto_offset_reset="latest",
        )
        raw_topic = app.topic(TOPIC, value_deserializer="json")
        session_topic = app.topic(SESSION_TOPIC, value_deserializer="json")
        config_events_topic = app.topic(CONFIG_EVENTS_TOPIC, value_deserializer="json")
    except Exception:
        logger.exception("Application/topic init failed; live consumer disabled")
        return

    # Map topic name → deserializer so the dispatcher below can pick the
    # right one without an isinstance check on the message object.
    deserializers = {
        raw_topic.name: raw_topic,
        session_topic.name: session_topic,
        config_events_topic.name: config_events_topic,
    }
    # Pre-warm the session + experiment caches from DCM so a backend restart
    # mid-AC-session can enrich raw ticks immediately, without waiting for
    # the user to start a new session. Best-effort: any failure is logged
    # and the loop starts anyway — fresh session messages still work.
    # The prewarm internally calls `_refresh_best_laps_from_settings()` once
    # the session+experiment caches are populated, which is when the
    # `(track, car, exp, env)` group set is finally known. Calling the
    # best-laps refresh BEFORE the prewarm would always find 0 groups and
    # waste a lake round-trip.
    _prewarm_session_cache_from_dcm()
    # Belt-and-braces: explicit refresh after the prewarm so the path
    # works even if the prewarm took a degenerate route (no DCM session
    # configs found → no internal refresh call). Failures are swallowed
    # inside `refresh_best_laps_cache`; if the lake is unreachable the
    # route layer's cache-miss fallback will retry on the next request.
    _refresh_best_laps_from_settings()

    logger.info(
        "ghost-lap consumer starting (topics=%s, %s, %s)",
        TOPIC,
        SESSION_TOPIC,
        CONFIG_EVENTS_TOPIC,
    )
    try:
        with app.get_consumer() as consumer:
            consumer.subscribe(
                [raw_topic.name, session_topic.name, config_events_topic.name]
            )
            while not _stop_event.is_set():
                msg = consumer.poll(timeout=0.5)
                if msg is None:
                    continue
                if msg.error():
                    logger.warning("kafka error: %s", msg.error())
                    continue
                topic_name = msg.topic()
                topic_obj = deserializers.get(topic_name)
                if topic_obj is None:
                    # Shouldn't happen given our subscription list, but guard
                    # so a misrouted message doesn't crash the loop.
                    continue
                try:
                    payload = topic_obj.deserialize(msg).value
                except Exception:
                    logger.debug("deserialize failed; skip", exc_info=True)
                    continue
                if not isinstance(payload, dict):
                    continue
                # Config events carry their target hostname inside
                # `metadata.target_key`, not as the Kafka key, so they
                # short-circuit the hostname extraction below.
                if topic_name == config_events_topic.name:
                    try:
                        _handle_config_event(payload)
                    except Exception:
                        logger.exception(
                            "handler error for topic=%s payload=%r",
                            topic_name,
                            payload,
                        )
                    continue
                # Kafka key on raw and session topics is the source's
                # hostname (see ac-telemetry-source/ac_source.py:139-141
                # for raw and the session producer call directly above
                # for sessions).
                raw_key = msg.key()
                hostname = (
                    (raw_key.decode() if isinstance(raw_key, bytes) else str(raw_key))
                    if raw_key is not None
                    else ""
                )
                if not hostname:
                    continue
                try:
                    if topic_name == session_topic.name:
                        _handle_session_message(hostname, payload)
                    else:
                        _handle_raw_message(hostname, payload)
                except Exception:
                    # Per-message handler errors must not kill the loop.
                    logger.exception(
                        "handler error for topic=%s hostname=%s", topic_name, hostname
                    )
    except Exception:
        logger.exception("ghost-lap consumer crashed; exiting thread")
    finally:
        logger.info("ghost-lap consumer stopped")


def start() -> None:
    """Start the consumer thread iff feature-flagged AND not LOCAL_DEV_MODE.

    Called from FastAPI lifespan startup. Idempotent: re-calling is a no-op
    while a thread is alive.
    """
    global _consumer_thread

    if os.getenv("LOCAL_DEV_MODE", "false").lower() == "true":
        logger.info("LOCAL_DEV_MODE=true — ghost-lap simulator only, no consumer.")
        return
    if os.getenv("LIVE_TELEMETRY_ENABLED", "false").lower() != "true":
        logger.info("LIVE_TELEMETRY_ENABLED!=true — ghost-lap live consumer disabled.")
        return
    if _consumer_thread and _consumer_thread.is_alive():
        return

    _stop_event.clear()
    _consumer_thread = threading.Thread(
        target=_consumer_loop,
        name="ghost-lap-consumer",
        daemon=True,
    )
    _consumer_thread.start()


def stop(timeout: float = 5.0) -> None:
    """Signal the consumer to exit and join with a bounded wait."""
    global _consumer_thread
    if not _consumer_thread:
        return
    _stop_event.set()
    _consumer_thread.join(timeout=timeout)
    if _consumer_thread.is_alive():
        logger.warning("ghost-lap consumer didn't stop within %.1fs", timeout)
    _consumer_thread = None
