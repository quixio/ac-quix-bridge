"""Unit tests for the seed reduction + grouping (no broker, no lake, no RocksDB)."""

from __future__ import annotations

from leaderboard_service_state.seed import (
    build_seed_messages,
    group_reduced_by_experiment,
    reduce_seed_rows,
)
from leaderboard_service_state.settings import Settings


def _settings() -> Settings:
    return Settings(
        sdk_token=None,
        broker_address="localhost:9092",
        consumer_group="t",
        raw_topic="raw",
        session_topic="session",
        config_topic="config",
        events_topic="leaderboard-events",
        config_api_url=None,
        dcm_timeout_s=5.0,
        lakehouse_query_url=None,
        lakehouse_query_token=None,
        lake_table="ac_telemetry",
        col_best_time="iBestTime",
        col_current_time="iCurrentTime",
        col_normalized_position="normalizedCarPosition",
        gate_count=10,
        state_dir="state",
        max_lap_samples=20000,
    )


def _lap_rows(env, exp, track, car, driver, lap, lap_ms):
    """Synthesize a full (>=95% coverage) linear lap as raw scan rows."""
    rows = []
    for i in range(101):
        pos = i / 100.0
        rows.append(
            {
                "environment": env,
                "experiment": exp,
                "track": track,
                "carModel": car,
                "driver": driver,
                "session_id": "S1",
                "lap": lap,
                "iCurrentTime": int(lap_ms * pos),
                "normalizedCarPosition": pos,
            }
        )
    return rows


def test_reduce_seed_rows_picks_fastest_complete_and_builds_vector():
    s = _settings()
    rows = (
        _lap_rows("rig", "EXP-1", "spa", "bmw_1m", "ada", 3, 95000)
        + _lap_rows("rig", "EXP-1", "spa", "bmw_1m", "ada", 5, 90000)  # faster
    )
    reduced = reduce_seed_rows(rows, s)
    key = ("rig", "EXP-1", "spa", "bmw_1m", "ada")
    assert key in reduced
    rec = reduced[key]
    assert rec["best_lap_ms"] == 90000  # the faster lap won
    assert rec["best_lap_number"] == 5
    assert len(rec["gate_vector"]) == 10
    assert rec["gate_vector"][-1] == 90000


def test_reduce_seed_rows_drops_partial_lap():
    s = _settings()
    # Only covers up to position 0.5 -> partial -> dropped.
    rows = [
        {
            "environment": "rig",
            "experiment": "EXP-1",
            "track": "spa",
            "carModel": "bmw_1m",
            "driver": "ada",
            "session_id": "S1",
            "lap": 2,
            "iCurrentTime": int(50000 * (i / 50.0)),
            "normalizedCarPosition": i / 100.0,
        }
        for i in range(51)
    ]
    reduced = reduce_seed_rows(rows, s)
    assert reduced == {}


def test_group_and_build_messages():
    s = _settings()
    rows = (
        _lap_rows("rig", "EXP-1", "spa", "bmw_1m", "ada", 3, 90000)
        + _lap_rows("rig", "EXP-2", "monza", "audi", "bo", 4, 100000)
    )
    reduced = reduce_seed_rows(rows, s)
    grouped = group_reduced_by_experiment(reduced)
    assert set(grouped) == {"EXP-1", "EXP-2"}
    assert grouped["EXP-1"]["environment"] == "rig"

    messages = {m["experiment"]: m for m in build_seed_messages(reduced)}
    assert set(messages) == {"EXP-1", "EXP-2"}
    msg = messages["EXP-1"]
    assert msg["type"] == "seed"
    assert len(msg["rows"]) == 1
    assert msg["rows"][0]["driver"] == "ada"
    assert len(msg["rows"][0]["gate_vector"]) == 10
