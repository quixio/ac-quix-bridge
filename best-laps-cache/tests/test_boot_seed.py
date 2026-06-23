"""Unit tests for the proactive boot seed (no broker, no RocksDB, no lake).

Covers:
* by-experiment grouping / seed-message build from a reduced-rows dict;
* lake failure / empty / no-URL → returns False;
* the ``type="seed"`` in-context fold gate (folds when State empty, skips when
  already populated).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd

from best_laps_cache.boot_seed import (
    build_seed_messages,
    group_reduced_by_experiment,
    run_boot_seed,
)
from best_laps_cache.mirror import BestLapsMirror
from best_laps_cache.pipeline import Pipeline
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
# grouping / payload build (pure functions)
# ---------------------------------------------------------------------------


def test_group_reduced_by_experiment_buckets_and_drops_blank():
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
    messages = {m["experiment"]: m for m in build_seed_messages(_REDUCED)}
    assert set(messages) == {"expA", "expB"}
    msg = messages["expA"]
    assert msg["type"] == "seed"
    assert msg["environment"] == "env"
    assert len(msg["rows"]) == 2


# ---------------------------------------------------------------------------
# run_boot_seed: lake query + seed production
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
def test_run_boot_seed_seeds_experiments(mock_lake_cls: MagicMock):
    """Lake has data → seed messages produced for each experiment."""
    mock_lake_cls.return_value.query.return_value = _lake_df()
    settings = _settings(lakehouse_query_url="http://lake", lakehouse_query_token="tok")
    produced: list[tuple[str, dict[str, Any]]] = []

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)))

    assert ran is True
    mock_lake_cls.return_value.query.assert_called_once()

    seed_msgs = [(k, m) for k, m in produced if m.get("type") == "seed"]
    assert len(seed_msgs) == 2
    seed_experiments = {m["experiment"] for _, m in seed_msgs}
    assert seed_experiments == {"expA", "expB"}
    # Each seed message keyed by its experiment.
    for key, msg in seed_msgs:
        assert key == msg["experiment"]

    # No mark_seeded or seed_gate messages.
    other_msgs = [(k, m) for k, m in produced if m.get("type") not in ("seed",)]
    assert other_msgs == []


def test_run_boot_seed_no_lake_url_returns_false():
    settings = _settings(lakehouse_query_url=None)
    produced: list[tuple[str, dict[str, Any]]] = []

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)))

    assert ran is False
    assert produced == []


@patch("best_laps_cache.boot_seed.LakehouseClient")
def test_run_boot_seed_lake_empty_returns_false(mock_lake_cls: MagicMock):
    mock_lake_cls.return_value.query.return_value = pd.DataFrame()
    settings = _settings(lakehouse_query_url="http://lake", lakehouse_query_token="tok")
    produced: list[tuple[str, dict[str, Any]]] = []

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)))

    assert ran is False
    assert produced == []


@patch("best_laps_cache.boot_seed.LakehouseClient")
def test_run_boot_seed_lake_failure_returns_false(mock_lake_cls: MagicMock):
    mock_lake_cls.return_value.query.side_effect = RuntimeError("connection refused")
    settings = _settings(lakehouse_query_url="http://lake", lakehouse_query_token="tok")
    produced: list[tuple[str, dict[str, Any]]] = []

    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)))

    assert ran is False
    assert produced == []


# ---------------------------------------------------------------------------
# type="seed" in-context fold gate (Pipeline._fold_seed_rows + _handle_event)
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
    assert changed is False
    assert [k for k in payload if k != "_env"] == []


def test_fold_seed_rows_does_not_clobber_better_existing():
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


def _make_pipeline_handler():
    """A Pipeline shell wired enough to exercise ``_handle_event`` offline."""
    settings = _settings()
    mirror = BestLapsMirror()
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._settings = settings
    pipeline._mirror = mirror
    return pipeline, mirror


def test_seed_handler_folds_when_state_empty():
    pipeline, mirror = _make_pipeline_handler()
    state = _FakeState()
    pipeline._handle_event(_seed_message(), state)
    payload = state.get("expA")
    assert payload["trk"]["car"] == {"Ada": 82345, "Bo": 91000}
    assert mirror.get("expA")["trk"]["car"] == {"Ada": 82345, "Bo": 91000}


def test_seed_handler_skips_when_state_populated():
    pipeline, mirror = _make_pipeline_handler()
    existing = {"_env": "env", "trk": {"car": {"Ada": 80000}}}
    state = _FakeState({"expA": dict(existing)})
    pipeline._handle_event(_seed_message(), state)
    # No clobber: populated experiment is left exactly as-is.
    assert state.get("expA") == existing
    # Mirror also not updated (seed skipped)
    assert mirror.get("expA") is None
