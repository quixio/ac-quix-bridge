"""Unit tests for the proactive boot seed (no broker, no RocksDB, no lake).

Covers:
* by-experiment grouping / seed-message build from a reduced-rows dict;
* the marker gate (skips the boot query when the marker file is present);
* the ``type="seed"`` in-context fold gate (folds when State empty, skips when
  already populated).
"""

from __future__ import annotations

import os
from typing import Any

from best_laps_cache.boot_seed import (
    build_seed_messages,
    group_reduced_by_experiment,
    marker_path,
    run_boot_seed,
    write_marker,
)
from best_laps_cache.pipeline import Pipeline
from best_laps_cache.settings import Settings

_REDUCED = {
    ("env", "expA", "trk", "car", "Ada"): 82345,
    ("env", "expA", "trk", "car", "Bo"): 91000,
    ("env", "expB", "spa", "gt3", "Cy"): 105000,
    ("", "", "trk", "car", "NoExp"): 70000,  # blank experiment -> skipped
}


def _settings(tmp_path) -> Settings:
    return Settings(
        sdk_token=None,
        broker_address="localhost:9092",
        consumer_group="test",
        raw_topic="raw",
        session_topic="session",
        config_topic="config",
        config_api_url=None,
        dcm_timeout_s=5.0,
        lakehouse_query_url=None,
        lakehouse_query_token=None,
        lake_table="ac_telemetry_prod",
        col_best_time="iBestTime",
        http_host="0.0.0.0",
        http_port=80,
        state_dir=str(tmp_path),
    )


# -- grouping / payload build ---------------------------------------------------


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


# -- marker gate ----------------------------------------------------------------


def test_run_boot_seed_skips_when_marker_present(tmp_path):
    settings = _settings(tmp_path)
    write_marker(settings)
    assert os.path.isfile(marker_path(settings))

    produced: list = []
    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)))
    assert ran is False
    assert produced == []  # lake never queried, nothing produced


def test_run_boot_seed_skips_when_no_lake_url(tmp_path):
    settings = _settings(tmp_path)  # lakehouse_query_url=None
    produced: list = []
    ran = run_boot_seed(settings, lambda k, m: produced.append((k, m)))
    assert ran is False
    assert not os.path.isfile(marker_path(settings))  # retry allowed later
    assert produced == []


# -- type="seed" in-context fold gate ------------------------------------------


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
    ``__new__`` and inject just the settings + a real MaterializedView. The
    seed/read branches of ``_handle_event`` never call Kafka — only State (faked)
    and the view — so this is sufficient and broker-free.
    """
    from best_laps_cache.materialized import MaterializedView

    settings = _settings(tmp_path)
    view = MaterializedView()
    pipeline = Pipeline.__new__(Pipeline)
    pipeline._settings = settings
    pipeline._view = view
    return pipeline, view


def test_seed_handler_folds_when_state_empty(tmp_path):
    pipeline, view = _make_pipeline_handler(tmp_path)
    state = _FakeState()  # empty -> seed should fold
    pipeline._handle_event(_seed_message(), state)
    payload = state.get("expA")
    assert payload["trk"]["car"] == {"Ada": 82345, "Bo": 91000}
    rows, _ = view.get_rows("expA")
    assert len(rows) == 2


def test_seed_handler_skips_when_state_populated(tmp_path):
    pipeline, _ = _make_pipeline_handler(tmp_path)
    existing = {"_env": "env", "trk": {"car": {"Ada": 80000}}}
    state = _FakeState({"expA": dict(existing)})
    pipeline._handle_event(_seed_message(), state)
    # No clobber: populated experiment is left exactly as-is.
    assert state.get("expA") == existing
