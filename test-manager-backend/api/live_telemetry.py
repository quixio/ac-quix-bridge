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
#                                  "fetched_epoch": float}
# populated synchronously when a session message arrives (one DCM HTTP call
# per session change); refreshed if older than EXPERIMENT_CACHE_TTL_S. Both
# `experiment` (content.experiment_id) and `driver` (content.driver, the
# canonical "who is driving this test" set by Test Manager) come from the
# same DCM experiment-config fetch — no extra HTTP calls vs the prior
# single-field cache.
_session_cache: dict[str, dict[str, str]] = {}
_experiment_cache: dict[str, dict[str, Any]] = {}

_consumer_thread: threading.Thread | None = None
_stop_event = threading.Event()

# ---------------------------------------------------------------------------
# Gate-vectors cache (per-driver best-lap per-gate cumulative times)
# ---------------------------------------------------------------------------
#
# Why this cache exists: `/api/v1/leaderboard/live-positions` is polled ~every
# 3.5 s while a user has the leaderboard tab open, but the underlying
# per-driver best laps in `ac_telemetry` only change when a new fast lap
# completes. We refresh on the natural "something changed" signal — a new
# `ac-telemetry-session` message — so the lake hit drops from "every poll"
# to "once per AC session start".
#
# Each `_HistoricalEntry` carries both the best-lap time
# (= `gate_vector[19]`, the lap-line gate) and the full 20-element per-gate
# cumulative-ms vector so the server can stamp a colour state onto the
# active row at every crossing without re-querying the lake.
#
# Cache shape:
#   {(track, carModel, experiment): {driver_folded: _HistoricalEntry}}
# Driver names are folded via `_fold_driver_name` (NFKD + ASCII lowercase)
# so a lake `"ludvik"` and a Mongo `"Ludvík"` collide on the same key.
#
# A `None` sentinel means "never refreshed yet" — distinct from "refreshed,
# but the lake had no rows" which is an empty dict. `build_live_positions`
# triggers a synchronous fallback refresh on `None`.
#
# Guarded by a dedicated `_gate_vectors_lock` rather than `_state_lock`
# because the lake query takes seconds and we must NOT block the raw-tick
# path. The refresh function builds the new dict outside the lock and only
# holds the lock long enough to swap the reference.
#
# This cache fully subsumes the retired `_historicals_cache`: any caller
# that previously needed `(best_lap_ms, best_lap_number)` reads them off
# `_HistoricalEntry`. See `docs/architecture-leaderboard-checkpoint-gates.md`.


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
# Live-mode state access
# ---------------------------------------------------------------------------


def _record_message(payload: dict[str, Any]) -> None:
    """Merge one raw Kafka payload into the per-(track, car, driver) state
    dict. Log and skip on missing fields — never crash the consumer loop.

    Maintains per-key `gate_times_ms` (length `GATE_COUNT`),
    `last_normalized_position`, and the sticky `last_gate_index` /
    `last_gate_state` / `last_gate_delta_ms` fields that the leaderboard
    assembly reads back unchanged between polls (server-side stickiness —
    see spec §5.4).

    On lap rollover (`completedLaps` increment OR `iCurrentTime` reset),
    `gate_times_ms` resets to `[None] * GATE_COUNT`, the previous position
    is rebased to 0, AND the sticky `last_gate_*` fields are cleared back
    to `None` so the previous lap's colour doesn't bleed into the new lap
    until the first gate crossing of the new lap (§8.7).

    Any gate crossed since the last tick is stamped with the current
    `iCurrentTime`. The colour-state computation itself lives in
    `leaderboard_real._compute_last_gate_state` and is invoked by the
    assembly layer per poll — this function only records the raw gate
    crossings.
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

        _update_gate_times(gate_times, prev_pos, norm_pos, i_current)
        entry_partial["gate_times_ms"] = gate_times
        # Sticky gate-state fields are written through unchanged here; the
        # assembly layer recomputes and persists them when it detects a new
        # crossing (see `leaderboard_real._compute_last_gate_state`).
        entry_partial["last_gate_index"] = last_gate_index
        entry_partial["last_gate_state"] = last_gate_state
        entry_partial["last_gate_delta_ms"] = last_gate_delta_ms
        _state[key] = entry_partial


def set_last_gate_state(
    track: str,
    car: str,
    driver: str,
    index: int | None,
    state: str | None,
    delta_ms: int | None,
) -> None:
    """Persist a recomputed gate-state triple onto the active driver's
    state entry so subsequent `get_active_driver()` calls re-emit the same
    sticky values until the next crossing.

    Called by `leaderboard_real._compute_last_gate_state` after it detects
    a new gate crossing and computes the colour. No-op when the key has
    no state entry yet (e.g. the active driver hasn't sent a tick).
    """
    key = (track, car, driver)
    with _state_lock:
        entry = _state.get(key)
        if entry is None:
            return
        entry["last_gate_index"] = index
        entry["last_gate_state"] = state
        entry["last_gate_delta_ms"] = delta_ms


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
    """Resolve the active experiment + driver for a hostname via the DCM HTTP API.

    Returns a dict `{"experiment_id": str, "driver": str}`. Both default to
    `""` when no experiment config exists for this hostname or any DCM call
    fails. `experiment_id` matches the Hive `experiment` partition in the
    lake (DCM content stores it under the legacy key `experiment_id`);
    `driver` is the canonical "who is driving this test" set by Test Manager
    (`api/routes/tests.py:sync_to_dcm` writes `content.driver = test.driver
    .lower()` alongside `experiment_id`). Sourcing both from the same single
    DCM call keeps the network cost identical to the prior experiment-only
    fetch.

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

    empty = {"experiment_id": "", "driver": ""}

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
            # uses as the `experiment` Hive partition) and `driver` (set by
            # `api/routes/tests.py:sync_to_dcm`, also the lake's `driver`
            # partition). See `tests.py` lines 91-92.
            experiment = str(content.get("experiment_id") or "")
            driver = str(content.get("driver") or "")
            logger.info(
                "DCM lookup OK: hostname=%s config=%s experiment=%r driver=%r",
                hostname,
                config_id,
                experiment,
                driver,
            )
            return {"experiment_id": experiment, "driver": driver}
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

    Final step: a one-shot `_refresh_gate_vectors_from_settings()` so the
    cached per-driver gate vectors reflect the drivers we just learned
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

        # Refresh gate-vectors once more so per-driver per-gate cumulative
        # times reflect the drivers we just discovered. Spec §5.3: cheap,
        # atomic, one extra lake hit at boot.
        if prewarmed_hostnames:
            _refresh_gate_vectors_from_settings()
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
    with _state_lock:
        _experiment_cache[hostname] = {
            "experiment": experiment,
            "driver": driver,
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
    # Same trigger for the gate-vectors cache — a new AC session means a
    # driver is about to set new lap times, so we want fresh per-gate
    # vectors in cache before the next `/live-positions` poll. Synchronous
    # is fine: this handler runs on the consumer thread, off the HTTP
    # request path, so the seconds-long lake call doesn't hurt API users.
    # Spec §5.3 trigger 1: canonical "once per AC session" refresh.
    _refresh_gate_vectors_from_settings()


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
            # And refresh gate vectors — a session-config change can mean a
            # different driver is about to log laps under this hostname.
            _refresh_gate_vectors_from_settings()
        else:
            # experiment-type event: update experiment_cache only. No
            # gate-vectors refresh — laps haven't changed.
            experiment = str(content.get("experiment_id") or "")
            driver = str(content.get("driver") or "")
            with _state_lock:
                _experiment_cache[target_key] = {
                    "experiment": experiment,
                    "driver": driver,
                    "fetched_epoch": time.time(),
                }
            logger.info(
                "config event applied: type=experiment hostname=%s "
                "experiment=%r driver=%r",
                target_key,
                experiment,
                driver,
            )
    except Exception:
        # Broad catch on purpose — handler errors must never break the loop.
        logger.exception("config event handler failed: %r", payload)


def get_gate_vectors_cache() -> (
    dict[tuple[str, str, str], dict[str, _HistoricalEntry]] | None
):
    """Return the current cached gate-vectors dict, or `None` if no
    refresh has run yet.

    Shape: `{(track, car, experiment): {driver_folded: _HistoricalEntry}}`.

    Callers must treat the returned dict as read-only — we hand back the
    live reference (cheap) rather than a deep copy. The cache is only
    swapped atomically via `refresh_gate_vectors_cache`, so a caller
    iterating the dict will see a consistent snapshot for the duration of
    that iteration even if a refresh races (the swap replaces the binding,
    not the dict contents in place).
    """
    with _gate_vectors_lock:
        return _gate_vectors_cache


def refresh_gate_vectors_cache(
    quixlake_url: str,
    quix_lake_token: str,
) -> None:
    """Query QuixLake for every historical's best-lap per-gate cumulative
    times and atomically swap the result into `_gate_vectors_cache`.

    Two lake queries (see spec §5.2): Query A finds each driver's best lap
    via the same per-lap aggregation today's `/best-laps` route uses;
    Query B fetches the raw position samples for those specific best laps
    so the Python reducer can pick the nearest sample per gate. QuixLake
    rejects `WITH` / CTE (`feedback_quixlake_no_cte`), so reduction
    happens in Python.

    Both queries and the Python reduction run OUTSIDE the lock — they
    take seconds and blocking other readers would defeat the purpose of
    the cache. Only the final reference swap is guarded.

    Lazy-imports `_query_lake` / `_reduce_to_per_driver_best` /
    `_query_gate_samples` / `_reduce_to_gate_vectors` from
    `routes.leaderboard_real` to keep import order one-way:
    `leaderboard_real` already imports `live_telemetry`, so importing it
    at module load here would cycle. The lazy import is paid once per
    refresh (rare event).

    All exceptions are caught and logged. If the refresh fails the
    previous cache value (possibly `None` on first run) stays valid, so
    the endpoint degrades to "missing data" rather than crashing.
    """
    try:
        from .routes.leaderboard_real import (
            _query_gate_samples,
            _query_lake,
            _reduce_to_gate_vectors,
            _reduce_to_per_driver_best,
        )

        raw_rows = _query_lake(quixlake_url, quix_lake_token)
        best_per_group = _reduce_to_per_driver_best(raw_rows)
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


def _refresh_gate_vectors_from_settings() -> None:
    """Internal: pull lake creds from `Settings` and refresh the cache.

    Used by the session-message handler and the consumer-startup warm-up
    so callers don't have to plumb settings through. Silently no-ops when
    credentials are missing — in that mode the route layer raises
    `LeaderboardError` anyway, so populating the cache is moot.
    """
    from .settings import get_settings

    settings = get_settings()
    if not settings.quixlake_url or not settings.quix_lake_token:
        logger.debug(
            "skipping gate-vectors cache refresh: QuixLake credentials not configured"
        )
        return
    refresh_gate_vectors_cache(settings.quixlake_url, settings.quix_lake_token)


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
    # Warm-up: pull gate vectors once at consumer startup so a backend
    # restart while AC is mid-session still has lap data available on the
    # very first `/live-positions` poll, without waiting for the next
    # session message. Failures are swallowed inside
    # `refresh_gate_vectors_cache`; if the lake is unreachable the route
    # layer's cache-miss fallback will retry on the next request.
    _refresh_gate_vectors_from_settings()
    # Pre-warm the session + experiment caches from DCM so a backend restart
    # mid-AC-session can enrich raw ticks immediately, without waiting for
    # the user to start a new session. Best-effort: any failure is logged
    # and the loop starts anyway — fresh session messages still work.
    _prewarm_session_cache_from_dcm()

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
