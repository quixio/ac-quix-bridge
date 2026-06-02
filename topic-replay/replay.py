"""Replay mode: stream a captured single lap of raw ticks back onto the
live Kafka topics, looping forever, with session + config re-asserted on
every loop boundary.

The producer uses `quixstreams.Application.get_producer()` directly — we
need byte-for-byte control over keys, headers, and inter-message timing.
Headers and keys come straight off the captured JSONL rows; we only deviate
from the capture when `TARGET_HOSTNAME_OVERRIDE` is set, in which case raw
and session keys are rewritten to that single hostname (config is left
alone — see spec §6.4).

Timing comes from each row's `timestamp_ms` field (Kafka message
timestamp). Inter-tick sleep is `(t[i+1] - t[i]) / 1000 / LAP_LOOP_SPEED`.
The producer is flushed periodically inside the loop, so a Ctrl-C unwinds
in at most ~1 s.
"""

from __future__ import annotations

import json
import logging
import signal
import time
from pathlib import Path

import kafka_client
from capture import (
    TOPIC_CONFIG,
    TOPIC_RAW,
    TOPIC_SESSION,
    _filename_for,
)
from lap_detection import find_single_lap

logger = logging.getLogger(__name__)

# Flush the producer roughly this often inside the raw-tick loop so a
# Ctrl-C delivers a clean shutdown without hanging on a full send buffer.
FLUSH_EVERY_N_TICKS = 30


class _StopReplay(Exception):
    """Raised by the signal handler to break out of the infinite loop."""


def _load_jsonl(path: Path) -> list[dict]:
    """Load a JSONL file into a list of decoded rows. Missing file returns
    an empty list (allows replay against a partial capture — e.g. config
    topic was empty during capture)."""
    if not path.exists():
        logger.warning("JSONL file not present: %s", path)
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning(
                    "Skipping malformed JSONL line %d in %s", line_no, path
                )
    logger.info("Loaded %d rows from %s", len(rows), path)
    return rows


def _encode_key(key: str | None, override: str | None) -> bytes | None:
    """Encode a Kafka key for produce. `override`, when non-empty, replaces
    the captured value verbatim — caller decides whether to pass it (raw +
    session topics get the override; config never does)."""
    effective = override if override else key
    if effective is None:
        return None
    return effective.encode("utf-8")


def _encode_headers(headers: list[list[str]]) -> list[tuple[str, bytes]]:
    """Encode JSONL-stored headers back to the `(str, bytes)` shape the
    Kafka producer expects."""
    return [(name, value.encode("utf-8")) for name, value in headers]


def _encode_value(value: object) -> bytes:
    """Re-serialise the captured value as compact UTF-8 JSON. Strings pass
    through (the capture fallback path stores raw-text values verbatim)."""
    if isinstance(value, str):
        return value.encode("utf-8")
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def _produce_row(producer, topic: str, row: dict, key_override: str | None) -> None:
    """Produce a single captured row to its destination topic. `topic` is the
    short name; kafka_client prepends the workspace ID."""
    producer.produce(
        topic=kafka_client.to_full_topic(topic),
        key=_encode_key(row.get("key"), key_override),
        value=_encode_value(row.get("value")),
        headers=_encode_headers(row.get("headers") or []),
    )


def _latest_session_per_key(session_rows: list[dict]) -> dict[str | None, dict]:
    """Return the last-seen session row per Kafka key, preserving insertion
    order (so the iteration order at loop-boundary re-emit matches the
    capture's natural order). Keyed by the *captured* key, not the
    overridden one — the override is applied at produce time."""
    latest: dict[str | None, dict] = {}
    for row in session_rows:
        latest[row.get("key")] = row
    return latest


def _icurrenttime(row: dict) -> int | None:
    """Pluck iCurrentTime from a JSONL row whose payload lives under `value`,
    or from a row that's already an unwrapped payload."""
    if "iCurrentTime" in row:
        return row["iCurrentTime"]
    v = row.get("value")
    return v.get("iCurrentTime") if isinstance(v, dict) else None


def _print_dry_run(raw_rows: list[dict], start: int, end: int) -> None:
    """Print the lap window summary for `--dry-run` and exit."""
    first_t = _icurrenttime(raw_rows[start])
    last_t = _icurrenttime(raw_rows[end - 1])
    first_ts = raw_rows[start].get("timestamp_ms")
    last_ts = raw_rows[end - 1].get("timestamp_ms")
    duration_ms = (
        last_ts - first_ts if first_ts is not None and last_ts is not None else None
    )
    print("Lap window detected:")
    print(f"  start_idx           = {start}")
    print(f"  end_idx             = {end}")
    print(f"  tick_count          = {end - start}")
    print(f"  first_iCurrentTime  = {first_t}")
    print(f"  last_iCurrentTime   = {last_t}")
    print(f"  lap_duration_ms     = {duration_ms}")


def _print_banner(target_hostname_override: str | None, speed: float) -> None:
    """Warn the operator that concurrent replay + live AC fights for the
    same backend state when no hostname override is in play."""
    print("=" * 72)
    print("topic-replay :: REPLAY MODE")
    print("=" * 72)
    print(f"Speed multiplier        : {speed}")
    print(f"TARGET_HOSTNAME_OVERRIDE: {target_hostname_override or '(none)'}")
    if not target_hostname_override:
        print(
            "WARNING: no TARGET_HOSTNAME_OVERRIDE set. If a live AC source is "
            "publishing for the SAME hostname this capture used, the backend "
            "leaderboard state will flap between the two streams. Set "
            "TARGET_HOSTNAME_OVERRIDE (or --target-hostname) to avoid this."
        )
    print("=" * 72, flush=True)


def run_replay(
    src_dir: Path,
    *,
    dry_run: bool = False,
    speed: float = 1.0,
    target_hostname_override: str | None = None,
) -> None:
    """Load capture, detect one lap, produce session+config once, then loop
    raw ticks forever (re-asserting session at each loop boundary)."""
    if speed <= 0:
        raise ValueError(f"LAP_LOOP_SPEED must be > 0, got {speed!r}")

    raw_path = src_dir / _filename_for(TOPIC_RAW)
    session_path = src_dir / _filename_for(TOPIC_SESSION)
    config_path = src_dir / _filename_for(TOPIC_CONFIG)

    raw_rows = _load_jsonl(raw_path)
    session_rows = _load_jsonl(session_path)
    config_rows = _load_jsonl(config_path)

    if not raw_rows:
        raise FileNotFoundError(
            f"No raw rows found in {raw_path}. Run capture first."
        )

    start_idx, end_idx = find_single_lap(raw_rows)

    if dry_run:
        _print_dry_run(raw_rows, start_idx, end_idx)
        return

    _print_banner(target_hostname_override, speed)

    producer = kafka_client.make_producer()

    # SIGTERM → same path as Ctrl-C. We re-raise as a sentinel so the
    # `finally` block always flushes the producer.
    def _on_sigterm(signum, frame):  # noqa: ARG001
        raise _StopReplay

    signal.signal(signal.SIGTERM, _on_sigterm)

    latest_session_rows = _latest_session_per_key(session_rows)

    try:
        # Startup emit: session first, then config. Config keys are NOT
        # rewritten (they target DCM, not the AC hostname namespace).
        for row in session_rows:
            _produce_row(producer, TOPIC_SESSION, row, target_hostname_override)
        for row in config_rows:
            _produce_row(producer, TOPIC_CONFIG, row, key_override=None)
        producer.flush()
        logger.info(
            "Startup emit complete: %d session + %d config rows",
            len(session_rows),
            len(config_rows),
        )

        # Infinite raw-tick loop.
        loop_count = 0
        while True:
            loop_count += 1
            loop_start = time.perf_counter()
            for i in range(start_idx, end_idx):
                row = raw_rows[i]
                _produce_row(producer, TOPIC_RAW, row, target_hostname_override)

                if (i - start_idx + 1) % FLUSH_EVERY_N_TICKS == 0:
                    producer.poll(0)

                # Sleep based on next-tick wall-clock spacing. Last tick of
                # the window doesn't sleep — we fall through to the
                # session re-emit.
                if i + 1 < end_idx:
                    t_now = row.get("timestamp_ms")
                    t_next = raw_rows[i + 1].get("timestamp_ms")
                    if t_now is not None and t_next is not None:
                        dt = max(0.0, (t_next - t_now) / 1000.0 / speed)
                        if dt > 0:
                            time.sleep(dt)

            # Re-emit the latest session row per captured key so the
            # backend's _session_cache never ages out across a loop.
            for row in latest_session_rows.values():
                _produce_row(
                    producer, TOPIC_SESSION, row, target_hostname_override
                )
            producer.flush()
            elapsed = time.perf_counter() - loop_start
            logger.info(
                "Lap %d complete in %.2fs (%d ticks @ speed=%.2f)",
                loop_count,
                elapsed,
                end_idx - start_idx,
                speed,
            )
    except (KeyboardInterrupt, _StopReplay):
        logger.info("Shutdown signal received; flushing producer.")
    finally:
        try:
            producer.flush(timeout=5)
        except Exception:
            logger.exception("Error flushing producer on shutdown")
