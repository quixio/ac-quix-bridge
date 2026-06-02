"""Capture mode: subscribe to the live Kafka topics and append each message
verbatim to JSONL files on disk.

The capture loop uses `quixstreams.Application.get_consumer()` directly
(rather than the higher-level `StreamingDataFrame` or `Source` APIs) so we
control message decoding, header preservation, and disk-write timing without
the framework's per-message side-effects.

Decoded row layout — one JSON object per line, per topic file:

    {
      "key": "<utf-8 hostname or null>",
      "value": { ...decoded JSON payload... },
      "headers": [["name", "value"], ...],
      "timestamp_ms": <kafka message timestamp>,
      "offset": <int>,
      "partition": <int>
    }

Three target files are opened up front (and refused if any already exist —
no silent overwrites of prior captures). Files are flushed and closed on
KeyboardInterrupt / SIGTERM. The loop prints per-topic row counts on
shutdown for at-a-glance health.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

import kafka_client

logger = logging.getLogger(__name__)

# Hardcoded topic names. These mirror `test-manager-backend/api/live_telemetry.py`
# and `ac-telemetry-source/app.yaml` — the consumer must subscribe to exactly
# the same set the backend listens on, so the JSONL set is a complete record
# of everything the leaderboard sees.
TOPIC_RAW = "ac-telemetry-raw"
TOPIC_SESSION = "ac-telemetry-session"
TOPIC_CONFIG = "ac-telemetry-config"

ALL_TOPICS: tuple[str, ...] = (TOPIC_RAW, TOPIC_SESSION, TOPIC_CONFIG)


def _filename_for(topic: str) -> str:
    """Map a Kafka topic name to its JSONL filename."""
    return f"{topic}.jsonl"


def _decode_key(raw: bytes | None) -> str | None:
    """Decode the Kafka message key as UTF-8. Returns `None` if the message
    had no key, or if the bytes are not valid UTF-8 (which should never
    happen on this pipeline — keys are always hostname strings)."""
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("Non-UTF-8 key encountered (len=%d); storing as null", len(raw))
        return None


def _decode_headers(
    headers: Iterable[tuple[str, bytes | str]] | None,
) -> list[list[str]]:
    """Decode Kafka headers into a JSON-serialisable list of `[name, value]`
    pairs. Header values arrive as bytes; we decode UTF-8 where possible and
    fall back to `repr(...)` for binary payloads (which we don't expect on
    this pipeline but want to preserve rather than crash on)."""
    if not headers:
        return []
    out: list[list[str]] = []
    for name, value in headers:
        if isinstance(value, bytes):
            try:
                decoded = value.decode("utf-8")
            except UnicodeDecodeError:
                decoded = repr(value)
        else:
            decoded = value
        out.append([name, decoded])
    return out


def _decode_value(raw: bytes | None) -> object:
    """Decode the Kafka message value. The AC source produces UTF-8 JSON, so
    we parse it back to a Python object for storage. If parsing fails we
    fall back to the raw UTF-8 string so the row is never lost."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Non-JSON value encountered (len=%d); storing as raw text", len(raw))
        return raw.decode("utf-8", errors="replace")


def run_capture(
    out_dir: Path,
    topics: tuple[str, ...] = ALL_TOPICS,
    offset: str = "latest",
    idle_timeout_s: float | None = None,
) -> None:
    """Run the capture loop until SIGINT / SIGTERM (or idle timeout).

    Creates one JSONL file per topic under `out_dir`. Refuses to start if
    any of the target files already exist.

    `offset` is the consumer's `auto.offset.reset`: `"latest"` for live
    capture, `"earliest"` to drain retained history. `idle_timeout_s`, if
    set, stops the loop after that many seconds without a single inbound
    message — useful with `earliest` to auto-finish once history is drained.
    """
    if offset not in {"latest", "earliest"}:
        raise ValueError(f"offset must be 'latest' or 'earliest', got {offset!r}")

    out_dir.mkdir(parents=True, exist_ok=True)

    targets: dict[str, Path] = {t: out_dir / _filename_for(t) for t in topics}
    existing = [p for p in targets.values() if p.exists()]
    if existing:
        raise FileExistsError(
            "Refusing to overwrite existing capture file(s): "
            + ", ".join(str(p) for p in existing)
        )

    # Unique consumer group per run — capture is read-only and we never
    # share offset state across runs. `scoped_consumer_group` adds the
    # workspace prefix in cloud mode and is a no-op in local mode (where
    # there is no workspace namespace).
    consumer_group_short = os.environ.get(
        "CAPTURE_CONSUMER_GROUP",
        f"topic-replay-capture-{uuid.uuid4().hex[:8]}",
    )
    consumer_group_full = kafka_client.scoped_consumer_group(consumer_group_short)

    consumer, full_to_short = kafka_client.make_consumer(
        list(topics),
        consumer_group=consumer_group_full,
        offset=offset,
    )

    counts: dict[str, int] = dict.fromkeys(topics, 0)

    # Install a SIGTERM handler that mirrors KeyboardInterrupt so the
    # `except` block below can flush files on either signal.
    def _on_sigterm(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_sigterm)

    handles: dict[str, TextIO] = {}
    try:
        for topic, path in targets.items():
            handles[topic] = path.open("w", encoding="utf-8")
            logger.info("Capture file open: %s", path)

        logger.info(
            "Capturing — group=%s offset=%s idle_timeout=%s. Press Ctrl-C to stop.",
            consumer_group_full,
            offset,
            idle_timeout_s,
        )

        last_msg_epoch = time.monotonic()
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                if (
                    idle_timeout_s is not None
                    and time.monotonic() - last_msg_epoch >= idle_timeout_s
                ):
                    logger.info(
                        "Idle for %.1fs; stopping capture.", idle_timeout_s
                    )
                    break
                continue
            if msg.error() is not None:
                logger.warning("Kafka poll error: %s", msg.error())
                continue
            last_msg_epoch = time.monotonic()

            short_topic = full_to_short.get(msg.topic())
            handle = handles.get(short_topic) if short_topic else None
            if handle is None:
                logger.warning(
                    "Message for unmapped topic %s; skipping", msg.topic()
                )
                continue

            row = {
                "key": _decode_key(msg.key()),
                "value": _decode_value(msg.value()),
                "headers": _decode_headers(msg.headers()),
                "timestamp_ms": msg.timestamp()[1] if msg.timestamp() else None,
                "offset": msg.offset(),
                "partition": msg.partition(),
            }
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            counts[short_topic] += 1

    except KeyboardInterrupt:
        logger.info("Shutdown signal received; closing files.")
    finally:
        try:
            consumer.close()
        except Exception:
            logger.exception("Error closing consumer")
        for handle in handles.values():
            try:
                handle.flush()
                handle.close()
            except OSError:
                logger.exception("Error closing capture file")

    print("Capture summary:", file=sys.stderr)
    for topic in topics:
        print(f"  {topic:30s} {counts[topic]:>8d} rows", file=sys.stderr)
