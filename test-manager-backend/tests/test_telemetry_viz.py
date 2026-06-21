import pytest

from shared.post_race_ai import telemetry_viz as tv


def test_format_lap_ms() -> None:
    assert tv.format_lap_ms(144795) == "2:24.795"
    assert tv.format_lap_ms(5000) == "0:05.000"
    assert tv.format_lap_ms(0) == "0:00.000"


def test_build_session_sql_clauses() -> None:
    sql = tv.build_session_sql("ac_telemetry_prod", "2026-06-19T11:06:54.186Z", "tomas eviltwin", "Spa", "porsche_991ii_gt3_r")
    assert "FROM ac_telemetry_prod" in sql
    assert "session_id = '2026-06-19T11:06:54.186Z'" in sql
    assert "driver = 'tomas eviltwin'" in sql
    assert "track = 'Spa'" in sql
    assert "carModel = 'porsche_991ii_gt3_r'" in sql
    assert "normalizedCarPosition AS pos" in sql


def test_build_session_sql_escapes_quote() -> None:
    sql = tv.build_session_sql("t", "s", "o'brien", "Spa", "car")
    assert "driver = 'o''brien'" in sql


def test_build_session_sql_rejects_bad_table() -> None:
    with pytest.raises(ValueError):
        tv.build_session_sql("t; DROP TABLE x", "s", "d", "Spa", "car")
