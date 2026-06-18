"""Unit tests for the pure nested-payload State helpers (no Kafka/RocksDB)."""

from __future__ import annotations

from leaderboard_service_state.state_model import (
    ENV_KEY,
    INT_MAX,
    count_stats,
    fold_best_lap,
    to_historicals,
    to_standings_rows,
)

_GV = [1000, 2000, 3000]


def test_fold_best_lap_first_write_changes():
    payload, changed = fold_best_lap(
        None, "spa", "bmw_1m", "ada", 91000, _GV, 7, environment="rig"
    )
    assert changed is True
    rec = payload["spa"]["bmw_1m"]["ada"]
    assert rec["best_lap_ms"] == 91000
    assert rec["best_lap_number"] == 7
    assert rec["gate_vector"] == _GV
    assert payload[ENV_KEY] == "rig"


def test_fold_best_lap_min_logic_replaces_whole_record():
    payload, _ = fold_best_lap(None, "spa", "bmw_1m", "ada", 91000, _GV, 7)
    # Slower lap: no change.
    payload, changed = fold_best_lap(
        payload, "spa", "bmw_1m", "ada", 92000, [9, 9, 9], 9
    )
    assert changed is False
    assert payload["spa"]["bmw_1m"]["ada"]["best_lap_ms"] == 91000
    # Equal lap: no change.
    _, changed = fold_best_lap(payload, "spa", "bmw_1m", "ada", 91000, [9, 9, 9], 9)
    assert changed is False
    # Faster lap: whole record replaced (new gate vector + lap number).
    faster_gv = [800, 1800, 2800]
    payload, changed = fold_best_lap(
        payload, "spa", "bmw_1m", "ada", 90000, faster_gv, 12
    )
    assert changed is True
    rec = payload["spa"]["bmw_1m"]["ada"]
    assert rec == {"best_lap_ms": 90000, "best_lap_number": 12, "gate_vector": faster_gv}


def test_fold_best_lap_filters_intmax_nonpositive_blank_and_empty_vector():
    assert fold_best_lap(None, "spa", "c", "d", INT_MAX, _GV, 1)[1] is False
    assert fold_best_lap(None, "spa", "c", "d", 0, _GV, 1)[1] is False
    assert fold_best_lap(None, "spa", "c", "", 90000, _GV, 1)[1] is False
    # Empty gate vector is a no-op (the store carries the vector, not raw samples).
    assert fold_best_lap(None, "spa", "c", "d", 90000, [], 1)[1] is False


def test_to_historicals_shape_matches_historical_entry_fields():
    payload, _ = fold_best_lap(None, "spa", "bmw_1m", "ada", 90000, _GV, 3, environment="r")
    payload, _ = fold_best_lap(payload, "spa", "bmw_1m", "bo", 92000, [1, 2, 4], 5, environment="r")
    hist = to_historicals("EXP-1", payload)
    group = hist[("spa", "bmw_1m", "EXP-1")]
    assert set(group) == {"ada", "bo"}
    assert group["ada"] == {"best_lap_ms": 90000, "best_lap_number": 3, "gate_vector": _GV}


def test_to_standings_rows_sorted_and_skips_env():
    payload, _ = fold_best_lap(None, "spa", "bmw_1m", "bo", 92000, _GV, 1, environment="r")
    payload, _ = fold_best_lap(payload, "spa", "bmw_1m", "ada", 90000, _GV, 1, environment="r")
    payload, _ = fold_best_lap(payload, "monza", "bmw_1m", "cy", 95000, _GV, 1, environment="r")
    rows = to_standings_rows("EXP-1", payload)
    assert len(rows) == 3
    assert rows[0]["track"] == "monza"
    assert rows[1]["driver"] == "ada" and rows[1]["best_lap_ms"] == 90000
    assert rows[2]["driver"] == "bo"
    assert all(r["experiment"] == "EXP-1" and r["environment"] == "r" for r in rows)


def test_count_stats():
    payload, _ = fold_best_lap(None, "spa", "bmw_1m", "ada", 90000, _GV, 1, environment="r")
    payload, _ = fold_best_lap(payload, "spa", "bmw_1m", "bo", 92000, _GV, 1)
    payload, _ = fold_best_lap(payload, "spa", "audi", "cy", 93000, _GV, 1)
    payload, _ = fold_best_lap(payload, "monza", "bmw_1m", "di", 94000, _GV, 1)
    tracks, car_groups, drivers = count_stats(payload)
    assert tracks == 2  # spa, monza (env marker excluded)
    assert car_groups == 3  # spa/bmw_1m, spa/audi, monza/bmw_1m
    assert drivers == 4
    assert count_stats(None) == (0, 0, 0)
    assert count_stats({ENV_KEY: "r"}) == (0, 0, 0)
