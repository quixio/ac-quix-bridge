"""Unit tests for the proactive boot seed (no broker, no RocksDB, no lake).

Covers:
* by-experiment grouping / seed-message build from a reduced-rows dict;
* the State-native gate via PendingRequests bridge (skip when seeded flag set,
  proceed when absent, proceed on gate timeout);
* lake failure / empty / no-URL → no mark_seeded, returns False;
* the ``type="seed"`` in-context fold gate (folds when State empty, skips when
  already populated).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd

from best_laps_cache.boot_seed import (
    GATE_KEY,
    build_seed_messages,
    group_reduced_by_experiment,
    run_boot_seed,
)
from best_laps_cache.pipeline import Pipeline
from best_laps_cache.request_bridge import PendingRequests
from best_laps_cache.settings import Settings

_REDUCED = {
    ("env", "expA", "trk", "car", "Ada"): 82345,
    ("env", "expA", "trk", "car", "Bo"): 91000,
    ("env", "expB", "spa", "gt3", "Cy"): 105000,
    ("", "", "trk", "car", "NoExp"): 70000,  # blank experiment -> skipped
}


def _settings(**overrides: Any) -> Settings:
    defaults = {
        "sdk_token": None,
        "broker_address": "localhost:9092",
        "consumer_group": "test",
        "raw_topic": "raw",
        "session_topic": "session",
        "config_topic": "config",
        "config_api_url": None,
        "dcm_timeout_s": 5.0,
        "lakehouse_query_url": None,
        "lakehouse_query_token": None,
        "lake_table": "ac_telemetry_prod",
        "col_best_time": "iBestTime",
        "http_host": "0.0.0.0",
        "http_port": 80,
        "state_dir": "state",
        "boot_seed_gate_timeout_s": 5.0,
    }
    defaults.update(overrides)
    return Settings(**defaults)


# ---------------------------------------------------------------------------
# Fake PendingRequests stub for controlling gate responses
# ---------------------------------------------------------------------------


class _FakePending:
    """Minimal stub that records ``open`` calls and returns a canned ``wait`` result."""

    def __init__(self, delivered: bool, payload: dict[str, Any] | None) -> None:
        self._delivered = delivered
        self._payload = payload
        self.opened: list[str] = []
        self._counter = 0

    def open(self) -> str:
        self._counter += 1
        req_id = f"fake-req-{self._counter}"
        self.opened.append(req_id)
        return req_id

    def wait(self, req_id: str, timeout: float) -> tuple[bool, dict[str, Any] | None]:
        return self._delivered, self._payload


# ---------------------------------------------------------------------------
# grouping / payload build (pure functions — unchanged, kept verbatim)
# ---------------------------------------------------------------------------


def test_group_reduced_by_experiment_buckets_and_drops_blank():
    """Validates spec §6.3: grouping step of build_seed_messages."""
    grouped = group_reduced_by_experiment(_REDUCED)
    assert set(grouped) == {"expA", "expB"}  # blank experiment dropped
    assert grouped["expA"]["environment"] == "env"
    rows_a = sorted(grouped["expA"]["rows"], key=lambda r: r["driver"])
    assert rows_a == [
        {"track": "trk", "carModel": "car", "driver": "Ada", "best_lap_ms": 82345},
        {"track": "trk", "carModel": "car", "driver": "Bo", "best_lap_ms": 91000},
    ]
    assert grouped["expB"]["rows"] == [
        {"track": "spa", "carModel": "gt3", "driver": "Cy", "best_lap_ms": 105000},
    ]


def test_build_seed_messages_shape():
    """Validates spec §6.3: build_seed_messages produces per-experiment dicts."""
    messages = {m["experiment"]: m for m in build_seed_messages(_REDUCED)}
    assert set(messages) == {"expA", "expB"}
    msg = messages["expA"]
    assert msg["type"] == "seed"
    assert msg["environment"] == "env"
    assert len(msg["rows"]) == 2


# ---------------------------------------------------------------------------
# Gate: already seeded → skip (spec §4 scenario 2, §5 step 2 "flag set → skip")
# ---------------------------------------------------------------------------


def test_run_boot_seed_skips_when_already_seeded():
    """Validates spec §5: seed_gate returns seeded=True -> skip lake query."""
    settings = _settings()
    produced: list[tuple[str, dict[str, Any]]] = []
    pending = _FakePending(delivered=True, payload={"seeded": True})

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)), pending)

    assert ran is False
    # The seed_gate probe is always produced, but no seed or mark_seeded messages.
    seed_msgs = [m for _, m in produced if m.get("type") == "seed"]
    mark_msgs = [m for _, m in produced if m.get("type") == "mark_seeded"]
    assert seed_msgs == []
    assert mark_msgs == []
    # Only the seed_gate probe was produced.
    assert len(produced) == 1
    assert produced[0][1]["type"] == "seed_gate"


# ---------------------------------------------------------------------------
# Gate: flag absent (seeded=False) → seed (spec §4 scenario 1, acceptance (a))
# ---------------------------------------------------------------------------


def _lake_df() -> pd.DataFrame:
    """A fake lake response with two experiments."""
    return pd.DataFrame(
        [
            {
                "environment": "env",
                "experiment": "expA",
                "track": "trk",
                "carModel": "car",
                "driver": "Ada",
                "iBestTime": 82345,
            },
            {
                "environment": "env",
                "experiment": "expA",
                "track": "trk",
                "carModel": "car",
                "driver": "Bo",
                "iBestTime": 91000,
            },
            {
                "environment": "env",
                "experiment": "expB",
                "track": "spa",
                "carModel": "gt3",
                "driver": "Cy",
                "iBestTime": 105000,
            },
        ]
    )


@patch("best_laps_cache.boot_seed.LakehouseClient")
def test_run_boot_seed_seeds_when_flag_absent(mock_lake_cls: MagicMock):
    """Validates spec §5 step 2-3: flag absent -> query lake, produce seeds + mark_seeded."""
    mock_lake_cls.return_value.query.return_value = _lake_df()
    settings = _settings(lakehouse_query_url="http://lake", lakehouse_query_token="tok")
    produced: list[tuple[str, dict[str, Any]]] = []
    pending = _FakePending(delivered=True, payload={"seeded": False})

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)), pending)

    assert ran is True
    mock_lake_cls.return_value.query.assert_called_once()

    # Per-experiment seed messages produced.
    seed_msgs = [(k, m) for k, m in produced if m.get("type") == "seed"]
    assert len(seed_msgs) == 2
    seed_experiments = {m["experiment"] for _, m in seed_msgs}
    assert seed_experiments == {"expA", "expB"}
    # Each seed message keyed by its experiment.
    for key, msg in seed_msgs:
        assert key == msg["experiment"]

    # mark_seeded produced last, keyed by GATE_KEY.
    mark_msgs = [(k, m) for k, m in produced if m.get("type") == "mark_seeded"]
    assert len(mark_msgs) == 1
    assert mark_msgs[0][0] == GATE_KEY
    assert mark_msgs[0][1]["experiment"] == GATE_KEY


# ---------------------------------------------------------------------------
# Gate timeout → proceed with seed (spec §5 step 2 "timeout → proceed",
# acceptance (d))
# ---------------------------------------------------------------------------


@patch("best_laps_cache.boot_seed.LakehouseClient")
def test_run_boot_seed_proceeds_on_gate_timeout(mock_lake_cls: MagicMock):
    """Validates spec §5: gate timeout → proceed (idempotent), same as fresh seed."""
    mock_lake_cls.return_value.query.return_value = _lake_df()
    settings = _settings(lakehouse_query_url="http://lake", lakehouse_query_token="tok")
    produced: list[tuple[str, dict[str, Any]]] = []
    # Simulate gate timeout: delivered=False, payload=None
    pending = _FakePending(delivered=False, payload=None)

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)), pending)

    assert ran is True
    mock_lake_cls.return_value.query.assert_called_once()

    seed_msgs = [(k, m) for k, m in produced if m.get("type") == "seed"]
    assert len(seed_msgs) == 2

    mark_msgs = [(k, m) for k, m in produced if m.get("type") == "mark_seeded"]
    assert len(mark_msgs) == 1
    assert mark_msgs[0][0] == GATE_KEY


# ---------------------------------------------------------------------------
# Lake empty / failure / no URL → no mark_seeded, returns False
# (spec §5 step 3 + docstring: "lake failure/empty/no-URL → return False,
# no mark_seeded")
# ---------------------------------------------------------------------------


def test_run_boot_seed_no_lake_url_returns_false():
    """Validates spec §6.3: no lakehouse URL → skip, no mark_seeded, retry allowed."""
    settings = _settings(lakehouse_query_url=None)
    produced: list[tuple[str, dict[str, Any]]] = []
    pending = _FakePending(delivered=True, payload={"seeded": False})

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)), pending)

    assert ran is False
    # No mark_seeded produced — flag stays unset so a later boot can retry.
    mark_msgs = [m for _, m in produced if m.get("type") == "mark_seeded"]
    assert mark_msgs == []
    # Only the seed_gate was produced (via the produce_event in run_boot_seed).
    seed_gate_msgs = [m for _, m in produced if m.get("type") == "seed_gate"]
    assert len(seed_gate_msgs) == 1


@patch("best_laps_cache.boot_seed.LakehouseClient")
def test_run_boot_seed_lake_empty_returns_false(mock_lake_cls: MagicMock):
    """Validates spec §6.3: lake returns 0 rows → return False, no mark_seeded."""
    mock_lake_cls.return_value.query.return_value = pd.DataFrame()
    settings = _settings(lakehouse_query_url="http://lake", lakehouse_query_token="tok")
    produced: list[tuple[str, dict[str, Any]]] = []
    pending = _FakePending(delivered=True, payload={"seeded": False})

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)), pending)

    assert ran is False
    mark_msgs = [m for _, m in produced if m.get("type") == "mark_seeded"]
    assert mark_msgs == []


@patch("best_laps_cache.boot_seed.LakehouseClient")
def test_run_boot_seed_lake_failure_returns_false(mock_lake_cls: MagicMock):
    """Validates spec §6.3: lake query raises → return False, no mark_seeded."""
    mock_lake_cls.return_value.query.side_effect = RuntimeError("connection refused")
    settings = _settings(lakehouse_query_url="http://lake", lakehouse_query_token="tok")
    produced: list[tuple[str, dict[str, Any]]] = []
    pending = _FakePending(delivered=True, payload={"seeded": False})

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)), pending)

    assert ran is False
    mark_msgs = [m for _, m in produced if m.get("type") == "mark_seeded"]
    assert mark_msgs == []


# ---------------------------------------------------------------------------
# Gate produces correct seed_gate event (spec §7: event shape)
# ---------------------------------------------------------------------------


def test_run_boot_seed_produces_seed_gate_event():
    """Validates spec §7: seed_gate event has correct shape."""
    settings = _settings()
    produced: list[tuple[str, dict[str, Any]]] = []
    pending = _FakePending(delivered=True, payload={"seeded": True})

    run_boot_seed(settings, lambda k, m: produced.append((k, m)), pending)

    assert len(produced) == 1
    key, msg = produced[0]
    assert key == GATE_KEY
    assert msg["type"] == "seed_gate"
    assert msg["experiment"] == GATE_KEY
    assert "req_id" in msg


# ---------------------------------------------------------------------------
# type="seed" in-context fold gate (Pipeline._fold_seed_rows + _handle_event)
# Kept verbatim from previous tests — these cover pipeline.py, not boot_seed.py,
# but are retained here as they were originally co-located.
# ---------------------------------------------------------------------------


def _seed_message():
    return {
        "type": "seed",
        "experiment": "expA",
        "environment": "env",
        "rows": [
            {"track": "trk", "carModel": "car", "driver": "Ada", "best_lap_ms": 82345},
            {"track": "trk", "carModel": "car", "driver": "Bo", "best_lap_ms": 91000},
        ],
    }


def test_fold_seed_rows_into_empty_payload():
    payload, changed = Pipeline._fold_seed_rows(_seed_message(), None)
    assert changed is True
    assert payload["_env"] == "env"
    assert payload["trk"]["car"] == {"Ada": 82345, "Bo": 91000}


def test_fold_seed_rows_drops_intmax_and_invalid():
    msg = {
        "type": "seed",
        "experiment": "expA",
        "environment": "env",
        "rows": [
            {"track": "trk", "carModel": "car", "driver": "X", "best_lap_ms": 2147483647},
            {"track": "", "carModel": "car", "driver": "Y", "best_lap_ms": 50000},
            "not-a-dict",
        ],
    }
    payload, changed = Pipeline._fold_seed_rows(msg, None)
    assert changed is False  # no real lap landed
    # fold_lap records the _env marker for an otherwise-valid row even when the
    # lap itself is dropped (blank track), but no track/car/driver entry exists.
    assert [k for k in payload if k != "_env"] == []


def test_fold_seed_rows_does_not_clobber_better_existing():
    # Existing State already faster for Ada; folding must not regress it.
    existing = {"_env": "env", "trk": {"car": {"Ada": 80000}}}
    payload, changed = Pipeline._fold_seed_rows(_seed_message(), existing)
    assert payload["trk"]["car"]["Ada"] == 80000  # kept the faster one
    assert payload["trk"]["car"]["Bo"] == 91000  # new driver added
    assert changed is True  # Bo is a new entry


class _FakeState:
    """Minimal in-memory stand-in for QuixStreams' State (no RocksDB)."""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._d: dict[str, Any] = dict(initial or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value


def _make_pipeline_handler(tmp_path):
    """A Pipeline shell wired enough to exercise ``_handle_event`` offline.

    ``app.topic()`` hits the broker, so we bypass ``__init__``/``_build`` via
    ``__new__`` and inject just the settings + a real PendingRequests bridge. The
    seed branch of ``_handle_event`` is write-only (touches only the faked State),
    so this is sufficient and broker-free.
    """
    settings = _settings()
    pending = PendingRequests()
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._settings = settings
    pipeline._pending = pending
    return pipeline, pending


def test_seed_handler_folds_when_state_empty(tmp_path):
    pipeline, _ = _make_pipeline_handler(tmp_path)
    state = _FakeState()  # empty -> seed should fold
    pipeline._handle_event(_seed_message(), state)
    payload = state.get("expA")
    assert payload["trk"]["car"] == {"Ada": 82345, "Bo": 91000}


def test_seed_handler_skips_when_state_populated(tmp_path):
    pipeline, _ = _make_pipeline_handler(tmp_path)
    existing = {"_env": "env", "trk": {"car": {"Ada": 80000}}}
    state = _FakeState({"expA": dict(existing)})
    pipeline._handle_event(_seed_message(), state)
    # No clobber: populated experiment is left exactly as-is.
    assert state.get("expA") == existing
