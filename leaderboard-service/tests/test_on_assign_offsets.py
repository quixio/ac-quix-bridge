"""Regression tests for the rebalance offset-override logic.

Bug: on restart with a lagging committed raw offset, the leaderboard
replayed old high-volume raw ticks; those fast old ticks kept
`_raw_feed_is_live()` true, so a stale session showed as live. The fix
seeks RAW partitions to `OFFSET_END` on every (re)assign while SESSION +
CONFIG stay at `OFFSET_BEGINNING`.

`_apply_assign_offsets` is the module-level, broker-free core of the
`_on_assign` rebalance callback. These tests feed it fake TopicPartition
objects and assert the offsets it stamps + that `assign()` is called once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pytest
from confluent_kafka import OFFSET_BEGINNING, OFFSET_END

os.environ.setdefault("MONGO_USER", "test")
os.environ.setdefault("MONGO_PASSWORD", "test")
os.environ.setdefault("Quix__Workspace__Id", "test-ws")
os.environ.setdefault("Quix__Sdk__Token", "test-token")
os.environ.setdefault("CONFIG_API_URL", "http://localhost:8001")

from api import live_telemetry  # noqa: E402

RAW = "ac-telemetry-raw"
SESSION = "ac-telemetry-session"
CONFIG = "ac-telemetry-config"


@dataclass
class _FakeTP:
    """Minimal confluent-kafka TopicPartition stand-in (mutable offset)."""

    topic: str
    partition: int = 0
    offset: int = -1001  # OFFSET_INVALID-ish sentinel


class _FakeConsumer:
    def __init__(self) -> None:
        self.assign_calls: list[list[_FakeTP]] = []

    def assign(self, partitions: list[_FakeTP]) -> None:
        # Snapshot offsets at call time so later mutation can't fool the test.
        self.assign_calls.append([_FakeTP(p.topic, p.partition, p.offset) for p in partitions])


def _run(partitions: list[_FakeTP]) -> tuple[_FakeConsumer, set[str], set[str]]:
    consumer = _FakeConsumer()
    rewound, tailed = live_telemetry._apply_assign_offsets(
        consumer,
        partitions,
        rewind_topics={SESSION, CONFIG},
        raw_topic_name=RAW,
    )
    return consumer, rewound, tailed


def test_raw_seeks_to_end_session_config_to_beginning():
    parts = [_FakeTP(RAW), _FakeTP(SESSION), _FakeTP(CONFIG)]
    consumer, rewound, tailed = _run(parts)

    by_topic = {p.topic: p.offset for p in parts}
    assert by_topic[RAW] == OFFSET_END
    assert by_topic[SESSION] == OFFSET_BEGINNING
    assert by_topic[CONFIG] == OFFSET_BEGINNING
    assert tailed == {RAW}
    assert rewound == {SESSION, CONFIG}


def test_assign_called_once_with_all_offsets():
    parts = [_FakeTP(RAW), _FakeTP(SESSION), _FakeTP(CONFIG)]
    consumer, _, _ = _run(parts)

    assert len(consumer.assign_calls) == 1
    assigned = {p.topic: p.offset for p in consumer.assign_calls[0]}
    assert assigned == {
        RAW: OFFSET_END,
        SESSION: OFFSET_BEGINNING,
        CONFIG: OFFSET_BEGINNING,
    }


def test_multiple_raw_partitions_all_tailed():
    parts = [_FakeTP(RAW, 0), _FakeTP(RAW, 1), _FakeTP(SESSION, 0)]
    _run(parts)

    assert [p.offset for p in parts if p.topic == RAW] == [OFFSET_END, OFFSET_END]
    assert [p.offset for p in parts if p.topic == SESSION] == [OFFSET_BEGINNING]


def test_committed_raw_offset_is_overridden():
    # Simulate a lagging committed offset on raw — it must be discarded.
    parts = [_FakeTP(RAW, 0, offset=42)]
    _run(parts)
    assert parts[0].offset == OFFSET_END


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
