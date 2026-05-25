"""Live-telemetry state store + Kafka consumer for the Ghost Lap leaderboard.

Two execution modes, switched by env:

  * `LOCAL_DEV_MODE=true`  → consumer thread NEVER runs. `get_active_driver()`
    returns a deterministic simulated lap so the frontend has motion to render
    without AC + Kafka.
  * `LIVE_TELEMETRY_ENABLED=true` (and `LOCAL_DEV_MODE` not `true`) →
    background thread runs a QuixStreams `Application` consuming
    `ac-telemetry-raw`. Last message per `(track, car, driver)` is kept in a
    module-level dict guarded by an RLock. Stale entries (>10 s old) are
    treated as "no active driver".

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


# Topic the AC source publishes to. Hardcoded because the source's
# `app.yaml` declares this name and we don't want yet another env var.
TOPIC = "ac-telemetry-raw"

# Stale-entry threshold: a driver that hasn't sent a tick in this many seconds
# is no longer "active". 10 s comfortably absorbs a brief pause / network
# blip but won't keep a long-gone session pinned.
STALE_AFTER_S = 10.0

# Consumer group + offset reset. `latest` is the right choice for "what is
# happening right now" — we don't care about history.
CONSUMER_GROUP = "test-manager-backend-ghost-lap"

# Number of equally-sized segments the lap is split into for the live ghost
# table. 10 → each segment covers 10% of normalizedCarPosition.
SEGMENT_COUNT = 10


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

_consumer_thread: threading.Thread | None = None
_stop_event = threading.Event()


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
    segment_cumulative = [samples[(i + 1) * 10]["time_ms"] for i in range(SEGMENT_COUNT)]
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
        topic = app.topic(TOPIC, value_deserializer="json")
    except Exception:
        logger.exception("Application/topic init failed; live consumer disabled")
        return

    logger.info("ghost-lap consumer starting (topic=%s)", TOPIC)
    try:
        with app.get_consumer() as consumer:
            consumer.subscribe([topic.name])
            while not _stop_event.is_set():
                msg = consumer.poll(timeout=0.5)
                if msg is None:
                    continue
                if msg.error():
                    logger.warning("kafka error: %s", msg.error())
                    continue
                try:
                    payload = topic.deserialize(msg).value
                except Exception:
                    logger.debug("deserialize failed; skip", exc_info=True)
                    continue
                if isinstance(payload, dict):
                    _record_message(payload)
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
