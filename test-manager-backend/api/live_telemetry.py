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
import math
import os
import threading
import time
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
TOPIC = "ac-telemetry-raw"
SESSION_TOPIC = "ac-telemetry-session"

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

# Number of equally-sized segments the lap is split into for the live ghost
# table. 10 → each segment covers 10% of normalizedCarPosition.
SEGMENT_COUNT = 10

# DCM experiment lookups: timeouts and a cache TTL fence. The TTL only
# matters as a safety net — the primary invalidation event is a new session
# message for the same hostname (which triggers a fresh fetch). 5 s timeout
# keeps a slow DCM from stalling the Kafka poll loop noticeably; the call
# happens on session-message arrival only, not on every tick.
DCM_TIMEOUT_S = 5.0
EXPERIMENT_CACHE_TTL_S = 300.0


def _update_segment_times(
    segment_times: list[int | None],
    prev_pos: float,
    new_pos: float,
    current_lap_time_ms: int,
) -> None:
    """Stamp `current_lap_time_ms` into every segment whose end boundary
    `(i+1)/SEGMENT_COUNT` was crossed between `prev_pos` (exclusive) and
    `new_pos` (inclusive). Mutates `segment_times` in place.

    If a single tick straddles multiple boundaries (sim coarse / network
    hiccup), all of them get the same timestamp — acceptable for V1.
    A boundary that already has a value is left untouched (first crossing
    wins, even if `normalized_position` wobbles backwards later).
    """
    for i in range(SEGMENT_COUNT):
        if segment_times[i] is not None:
            continue
        boundary = (i + 1) / SEGMENT_COUNT
        if new_pos >= boundary > prev_pos:
            segment_times[i] = current_lap_time_ms


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
# `_experiment_cache[hostname]` = {"experiment": str, "fetched_epoch": float}
# populated synchronously when a session message arrives (one DCM HTTP call
# per session change); refreshed if older than EXPERIMENT_CACHE_TTL_S.
_session_cache: dict[str, dict[str, str]] = {}
_experiment_cache: dict[str, dict[str, Any]] = {}

_consumer_thread: threading.Thread | None = None
_stop_event = threading.Event()

# ---------------------------------------------------------------------------
# Historicals cache (per-driver-best from QuixLake)
# ---------------------------------------------------------------------------
#
# Why this cache exists: `/api/v1/leaderboard/live-positions` is polled ~every
# 8 s while a user has the leaderboard tab open, but the underlying per-driver
# best laps in `ac_telemetry` only change when a new fast lap completes. We
# refresh on the natural "something changed" signal — a new
# `ac-telemetry-session` message — so the lake hit drops from "every poll"
# to "once per AC session start".
#
# Cache shape is identical to `leaderboard_real._reduce_to_per_driver_best`'s
# return so the existing assembly code doesn't change shape:
#   {(track, carModel, experiment, driver): (best_ms, best_lap_number)}
#
# A `None` sentinel means "never refreshed yet" — distinct from "refreshed,
# but the lake had no rows" which is an empty dict. `build_live_positions`
# triggers a synchronous fallback refresh on `None`.
#
# Guarded by a dedicated `_historicals_lock` rather than `_state_lock`
# because the lake query takes seconds and we must NOT block the raw-tick
# path. The refresh function builds the new dict outside the lock and only
# holds the lock long enough to swap the reference.
_historicals_lock = threading.RLock()
_historicals_cache: dict[tuple[str, str, str, str], tuple[int, int]] | None = None


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
# Sim's segment-crossing state. Mirrors the real-mode per-key state.
_sim_segment_times_ms: list[int | None] = [None] * SEGMENT_COUNT
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

    Also maintains `_sim_segment_times_ms` so the segment-breakdown table
    has populated "completed" rows. The crossings are evaluated on every
    call — provided clients poll at 500 ms, every 10% boundary is observed
    inside the same lap (the sim's lap is ~91 s long, so each 9.1 s segment
    contains ~18 polls).
    """
    global _sim_start_epoch, _sim_last_norm_pos, _sim_last_lap_index
    global _sim_segment_times_ms

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

        # Segment-time bookkeeping. Reset on lap rollover, then stamp any
        # boundaries crossed since the previous call.
        completed = len(_sim_completed_lap_times_ms)
        if completed != _sim_last_lap_index:
            _sim_segment_times_ms = [None] * SEGMENT_COUNT
            _sim_last_norm_pos = 0.0
            _sim_last_lap_index = completed
        _update_segment_times(
            _sim_segment_times_ms, _sim_last_norm_pos, norm_pos, elapsed_ms
        )
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
            "segment_times_ms": list(_sim_segment_times_ms),
            "last_normalized_position": _sim_last_norm_pos,
        }


def simulated_ghost_reference(driver: str, car: str, track: str) -> dict[str, Any]:
    """Synthesise a 101-sample ghost lap deterministically from the driver
    name.

    Total lap time is tuned ~200 ms slower than the live sim's lap target so
    the global delta is small (≈ 0 at the end). A larger sinusoidal wiggle
    (8% amplitude) on top makes the running delta cross zero several times
    within a single lap, so the arrow flips between ahead and behind even
    though both curves are deterministic. This is what gives the UI
    something visible to show in LOCAL_DEV_MODE.
    """
    # Driver-seeded tiny ±300 ms offset so different driver names produce
    # different reference best laps, but the bulk pacing always lands close
    # to the live sim target.
    seed = sum(ord(c) for c in driver) % 7
    # Ghost is ~ live target so the delta hovers near 0 across the lap.
    base_lap_ms = _SIM_LAP_TARGET_MS + (seed - 3) * 100  # 90.7..91.3 s
    samples: list[dict[str, Any]] = []
    for i in range(101):
        frac = i / 100.0
        # ±2.5 s additive wiggle, ~2 cycles per lap. Period in time ≈ 45 s
        # so a 30-second observation window (3 shots, 15 s apart) clears
        # one zero-crossing → the arrow flips ahead↔behind cleanly.
        wiggle_ms = 2500 * math.sin(4.0 * math.pi * frac)
        t = base_lap_ms * frac + wiggle_ms
        if t < 0:
            t = 0
        samples.append({"pos": round(frac, 2), "time_ms": int(t)})
    # Pick samples at indices 10, 20, ..., 100 — pos 0.10..1.00, the END of
    # each of the 10 segments.
    segment_cumulative = [
        samples[(i + 1) * 10]["time_ms"] for i in range(SEGMENT_COUNT)
    ]
    return {
        "driver": driver,
        "car": car,
        "track": track,
        "best_lap_ms": base_lap_ms,
        "samples": samples,
        "source_session_id": "sim-session-2024-01-01T00-00-00Z",
        "source_lap": 1,
        "segment_cumulative_ms": segment_cumulative,
    }


def segment_cumulative_from_samples(samples: list[dict[str, Any]]) -> list[int]:
    """Extract cumulative ms at positions 0.10..1.00 from a 101-sample curve.

    Returns an empty list when the input doesn't have the expected shape; the
    Pydantic model then defaults to `[]` and the frontend renders `—` for
    the Reference column rather than crashing.
    """
    if len(samples) != 101:
        return []
    out: list[int] = []
    for i in range(SEGMENT_COUNT):
        idx = (i + 1) * 10
        try:
            out.append(int(samples[idx]["time_ms"]))
        except (KeyError, TypeError, ValueError):
            return []
    return out


# ---------------------------------------------------------------------------
# Live-mode state access
# ---------------------------------------------------------------------------


def _record_message(payload: dict[str, Any]) -> None:
    """Merge one raw Kafka payload into the per-(track, car, driver) state
    dict. Log and skip on missing fields — never crash the consumer loop.

    Maintains per-key `segment_times_ms` (length `SEGMENT_COUNT`) and
    `last_normalized_position`. On lap rollover (`completedLaps` increment),
    `segment_times_ms` resets to `[None] * SEGMENT_COUNT` and the previous
    position is rebased to 0. Any segment boundary crossed since the last
    tick is stamped with the current `iCurrentTime`.
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
        entry_partial = {
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
        if prev is None or prev.get("completedLaps", -1) != completed:
            segment_times: list[int | None] = [None] * SEGMENT_COUNT
            prev_pos = 0.0
        else:
            segment_times = list(prev.get("segment_times_ms") or [None] * SEGMENT_COUNT)
            if len(segment_times) != SEGMENT_COUNT:
                segment_times = [None] * SEGMENT_COUNT
            prev_pos = float(prev.get("normalizedCarPosition") or 0.0)

        _update_segment_times(segment_times, prev_pos, norm_pos, i_current)
        entry_partial["segment_times_ms"] = segment_times
        _state[key] = entry_partial


def get_active_driver() -> dict[str, Any] | None:
    """Return the freshest non-stale entry as a `LiveDriverState`-shaped
    dict, or None if nothing has been seen in the last `STALE_AFTER_S`.
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
    segment_times = entry.get("segment_times_ms") or [None] * SEGMENT_COUNT
    return {
        "driver": driver,
        "car": car,
        "track": track,
        "experiment": entry["experiment"],
        "current_lap": (entry["completedLaps"] or 0) + 1,
        "current_lap_time_ms": entry["iCurrentTime"],
        "normalized_position": entry["normalizedCarPosition"],
        "best_lap_ms_session": best,
        "segment_times_ms": list(segment_times),
        "last_normalized_position": float(entry["normalizedCarPosition"]),
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


def _fetch_experiment_from_dcm(hostname: str) -> str:
    """Resolve the active experiment for a hostname via the DCM HTTP API.

    Returns the experiment string (matches the Hive `experiment` partition
    in the lake; DCM content stores it as `experiment_id`) or `""` if no
    experiment config exists for this hostname or any DCM call fails.

    Network-blocking but called only on session-message arrival (rare event,
    not per-tick). All exceptions are caught here — the consumer loop must
    never crash because DCM is briefly unavailable. A miss caches an empty
    string so we don't hammer DCM for hosts that genuinely have no config.

    Adapted from `session-config-bridge/main.py:107-161`; we don't import
    from that service per the spec.
    """
    # Imported lazily so the simulator and tests that don't run the consumer
    # don't pay the import cost.
    import httpx

    from .settings import get_settings

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
                return ""
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
                return ""

            # 2. Pick the latest version and fetch its content.
            content = _fetch_latest_version_content(client, base, config_id, headers)
            if content is None:
                return ""

            # Content key is `experiment_id` (see test-manager-backend
            # `tests.py:91`). That string is the same value the lake uses as
            # the `experiment` Hive partition.
            experiment = str(content.get("experiment_id") or "")
            logger.info(
                "DCM lookup OK: hostname=%s config=%s experiment=%r",
                hostname,
                config_id,
                experiment,
            )
            return experiment
    except Exception:
        # Catch broad on purpose — httpx errors, JSON decode errors, etc.
        # Caller treats "" as "unknown experiment".
        logger.exception("DCM experiment lookup failed for hostname=%s", hostname)
        return ""


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

    Why DCM and not Kafka with `auto_offset_reset="earliest"`: session-topic
    retention may include long-dead hostnames; DCM stores exactly one
    "current session" per hostname, which is what we want.

    Final step: a one-shot `_refresh_historicals_from_settings()` so the
    cached per-driver-bests reflect the drivers we just learned about. The
    extra lake hit (on top of the consumer's existing startup warm-up) is
    bounded — one extra call per backend boot, cache swap is atomic.
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

        # Refresh historicals once more so per-driver-bests reflect the
        # drivers we just discovered. Spec §5: cheap, atomic, one extra
        # lake hit at boot.
        if prewarmed_hostnames:
            _refresh_historicals_from_settings()
    except Exception:
        logger.exception("DCM session pre-warm failed; continuing without it")


def _get_cached_experiment(hostname: str, force_refresh: bool = False) -> str:
    """Return the cached experiment for a hostname, refreshing on TTL expiry
    or `force_refresh=True` (used when a new session message arrives).

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
    experiment = _fetch_experiment_from_dcm(hostname)
    with _state_lock:
        _experiment_cache[hostname] = {
            "experiment": experiment,
            "fetched_epoch": time.time(),
        }
    return experiment


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
    # Same trigger for the historicals cache — a new AC session means a
    # driver is about to set new lap times, so we want fresh per-driver
    # bests in cache before the next `/live-positions` poll. Synchronous
    # is fine: this handler runs on the consumer thread, off the HTTP
    # request path, so the seconds-long lake call doesn't hurt API users.
    _refresh_historicals_from_settings()


def get_historicals_cache() -> dict[tuple[str, str, str, str], tuple[int, int]] | None:
    """Return the current cached per-driver-best dict, or `None` if no
    refresh has run yet.

    Callers must treat the returned dict as read-only — we hand back the
    live reference (cheap) rather than a deep copy. The cache is only
    swapped atomically via `refresh_historicals_cache`, so a caller iterating
    the dict will see a consistent snapshot for the duration of that
    iteration even if a refresh races (the swap replaces the binding, not
    the dict contents in place).
    """
    with _historicals_lock:
        return _historicals_cache


def refresh_historicals_cache(
    quixlake_url: str,
    quix_lake_token: str,
) -> None:
    """Pull the per-driver-best reduction from QuixLake and atomically
    swap it into `_historicals_cache`.

    The lake query and Python reduction run OUTSIDE the lock — the query
    takes seconds and blocking other readers would defeat the purpose of
    the cache. Only the final reference swap is guarded.

    Lazy-imports `_query_lake` / `_reduce_to_per_driver_best` from
    `routes.leaderboard_real` to keep import order one-way:
    `leaderboard_real` already imports `live_telemetry`, so importing it
    at module load here would cycle. The lazy import is paid once per
    refresh (rare event).

    All exceptions are caught and logged. If the refresh fails the
    previous cache value (possibly `None` on first run) stays valid, so
    the endpoint degrades to "missing data" rather than crashing.
    """
    try:
        from .routes.leaderboard_real import _query_lake, _reduce_to_per_driver_best

        raw_rows = _query_lake(quixlake_url, quix_lake_token)
        reduced = _reduce_to_per_driver_best(raw_rows)
    except Exception:
        logger.exception("historicals cache refresh failed; keeping previous cache")
        return

    with _historicals_lock:
        global _historicals_cache
        _historicals_cache = reduced
    logger.info(
        "historicals cache refreshed: %d (track,car,exp,driver) groups",
        len(reduced),
    )


def _refresh_historicals_from_settings() -> None:
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
            "skipping historicals cache refresh: QuixLake credentials not configured"
        )
        return
    refresh_historicals_cache(settings.quixlake_url, settings.quix_lake_token)


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
    # AC's static struct calls the driver `playerName`; downstream code uses
    # `driver`. Prefer an explicit `driver` if the raw payload ever grows
    # one (defensive — current source doesn't set it).
    if not enriched.get("driver"):
        enriched["driver"] = session["playerName"]
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
    except Exception:
        logger.exception("Application/topic init failed; live consumer disabled")
        return

    # Map topic name → deserializer so the dispatcher below can pick the
    # right one without an isinstance check on the message object.
    deserializers = {
        raw_topic.name: raw_topic,
        session_topic.name: session_topic,
    }
    # Warm-up: pull historicals once at consumer startup so a backend restart
    # while AC is mid-session still has lap data available on the very first
    # `/live-positions` poll, without waiting for the next session message.
    # Failures are swallowed inside `refresh_historicals_cache`; if the lake
    # is unreachable the route layer's cache-miss fallback will retry on the
    # next request.
    _refresh_historicals_from_settings()
    # Pre-warm the session + experiment caches from DCM so a backend restart
    # mid-AC-session can enrich raw ticks immediately, without waiting for
    # the user to start a new session. Best-effort: any failure is logged
    # and the loop starts anyway — fresh session messages still work.
    _prewarm_session_cache_from_dcm()

    logger.info("ghost-lap consumer starting (topics=%s, %s)", TOPIC, SESSION_TOPIC)
    try:
        with app.get_consumer() as consumer:
            consumer.subscribe([raw_topic.name, session_topic.name])
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
                # Kafka key on both topics is the source's hostname (see
                # ac-telemetry-source/ac_source.py:139-141 for raw and the
                # session producer call directly above for sessions).
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
