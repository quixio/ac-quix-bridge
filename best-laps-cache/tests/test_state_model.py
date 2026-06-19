"""Unit tests for the pure nested-payload State helpers (no Kafka/RocksDB)."""

from __future__ import annotations

from best_laps_cache.state_model import (
    ENV_KEY,
    INT_MAX,
    filter_rows,
    fold_lap,
    to_rows,
)


def test_fold_lap_first_write_changes():
    payload, changed = fold_lap(None, "spa", "bmw_1m", "Ada", 91000, environment="env")
    assert changed is True
    assert payload["spa"]["bmw_1m"]["Ada"] == 91000
    assert payload[ENV_KEY] == "env"


def test_fold_lap_min_logic():
    payload, _ = fold_lap(None, "spa", "bmw_1m", "Ada", 91000)
    # Slower lap: no change.
    payload, changed = fold_lap(payload, "spa", "bmw_1m", "Ada", 92000)
    assert changed is False
    assert payload["spa"]["bmw_1m"]["Ada"] == 91000
    # Equal lap: no change.
    _, changed = fold_lap(payload, "spa", "bmw_1m", "Ada", 91000)
    assert changed is False
    # Faster lap: change.
    payload, changed = fold_lap(payload, "spa", "bmw_1m", "Ada", 90000)
    assert changed is True
    assert payload["spa"]["bmw_1m"]["Ada"] == 90000


def test_fold_lap_filters_int_max_and_nonpositive():
    payload, changed = fold_lap(None, "spa", "bmw_1m", "Ada", INT_MAX)
    assert changed is False
    assert "spa" not in payload
    payload, changed = fold_lap(None, "spa", "bmw_1m", "Ada", 0)
    assert changed is False
    payload, changed = fold_lap(None, "spa", "bmw_1m", "", 90000)
    assert changed is False


def test_to_rows_sorted_and_skips_env_marker():
    payload, _ = fold_lap(None, "spa", "bmw_1m", "Bo", 92000, environment="env1")
    payload, _ = fold_lap(payload, "spa", "bmw_1m", "Ada", 90000, environment="env1")
    payload, _ = fold_lap(payload, "monza", "bmw_1m", "Cy", 95000, environment="env1")
    rows = to_rows("EXP-1", payload)
    # 3 rows, env marker not surfaced as a track.
    assert len(rows) == 3
    # Sorted by (track, carModel, ms): monza first, then spa fastest-first.
    assert rows[0]["track"] == "monza"
    assert rows[1]["driver"] == "Ada" and rows[1]["best_lap_ms"] == 90000
    assert rows[2]["driver"] == "Bo"
    assert all(r["experiment"] == "EXP-1" for r in rows)
    assert all(r["environment"] == "env1" for r in rows)


def test_to_rows_empty():
    assert to_rows("EXP-1", None) == []
    assert to_rows("EXP-1", {ENV_KEY: "env"}) == []


def test_filter_rows_by_track_and_car():
    payload, _ = fold_lap(None, "spa", "bmw_1m", "Ada", 90000)
    payload, _ = fold_lap(payload, "spa", "audi_r8", "Bo", 91000)
    payload, _ = fold_lap(payload, "monza", "bmw_1m", "Cy", 95000)
    rows = to_rows("EXP-1", payload)
    assert len(filter_rows(rows, track="spa")) == 2
    assert len(filter_rows(rows, track="spa", car_model="bmw_1m")) == 1
    assert filter_rows(rows, track="spa", car_model="bmw_1m")[0]["driver"] == "Ada"
    assert len(filter_rows(rows, track="nope")) == 0
    assert len(filter_rows(rows)) == 3  # no filter = all
