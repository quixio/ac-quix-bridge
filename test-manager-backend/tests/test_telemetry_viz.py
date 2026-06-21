import numpy as np
import pandas as pd
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


def _lap_df(
    lap: int,
    n: int,
    *,
    pos_start: float = 0.0,
    pos_end: float = 1.0,
    invalid: int = 0,
    speed: float = 200.0,
    lap_ms: int = 100000,
) -> pd.DataFrame:
    """One synthetic lap: monotonic pos pos_start..pos_end, ict ramps to lap_ms."""
    pos = np.linspace(pos_start, pos_end, n)
    return pd.DataFrame(
        {
            "lap": lap,
            "pos": pos,
            "speedKmh": np.full(n, speed),
            "gas": np.full(n, 0.8),
            "brake": np.zeros(n),
            "gear": np.full(n, 4),
            "iCurrentTime": np.linspace(0, lap_ms, n).astype(int),
            "isValidLap": np.array([0 if i < invalid else 1 for i in range(n)]),
            "timestamp_ms": np.arange(n) * 20,
        }
    )


def test_clean_laps_drops_last_lap_and_sliver() -> None:
    df = pd.concat([
        _lap_df(1, 5000, lap_ms=100000),
        _lap_df(2, 5000, lap_ms=99000),
        _lap_df(3, 300),   # sliver (<=1000) — but also not last; dropped as sliver
        _lap_df(4, 200),   # last lap — dropped
    ], ignore_index=True)
    series = tv.clean_laps(df)
    kept = [lp.lap for lp in series.laps]
    assert kept == [1, 2]  # 3 sliver-dropped, 4 last-dropped


def test_clean_laps_fastest_valid() -> None:
    df = pd.concat([
        _lap_df(1, 5000, lap_ms=100000, invalid=0),  # valid, slower
        _lap_df(2, 5000, lap_ms=98000, invalid=3000),  # faster but INVALID
        _lap_df(3, 5000, lap_ms=99000, invalid=0),  # valid, fastest valid
        _lap_df(4, 200),  # last, dropped
    ], ignore_index=True)
    series = tv.clean_laps(df)
    assert series.fastest_valid_idx is not None
    assert series.laps[series.fastest_valid_idx].lap == 3


def test_clean_laps_no_valid_lap() -> None:
    df = pd.concat([
        _lap_df(1, 5000, invalid=4000),
        _lap_df(2, 5000, invalid=4000),
        _lap_df(3, 200),  # last
    ], ignore_index=True)
    series = tv.clean_laps(df)
    assert len(series.laps) == 2
    assert series.fastest_valid_idx is None


def test_clean_laps_trims_lap1_staging() -> None:
    # lap 1: staging 0.9->1.0 then wrap to 0.0->1.0 (non-monotonic in time)
    staging = _lap_df(1, 1500, pos_start=0.9, pos_end=1.0)
    flying = _lap_df(1, 5000, pos_start=0.0, pos_end=1.0)
    flying["timestamp_ms"] = flying["timestamp_ms"] + 100000  # later in time
    df = pd.concat([staging, flying, _lap_df(2, 200)], ignore_index=True)
    series = tv.clean_laps(df)
    assert len(series.laps) == 1
    # after trim+sort+downsample, pos is monotonic non-decreasing
    pos = series.laps[0].pos
    assert all(pos[i] <= pos[i + 1] + 1e-9 for i in range(len(pos) - 1))


def test_clean_laps_downsamples() -> None:
    df = pd.concat([_lap_df(1, 8000), _lap_df(2, 200)], ignore_index=True)
    series = tv.clean_laps(df, n_bins=400)
    assert 0 < len(series.laps[0].pos) <= 400


def test_clean_laps_empty() -> None:
    assert tv.clean_laps(pd.DataFrame()).laps == []


def test_render_none_when_empty() -> None:
    assert tv.render_telemetry_svg(tv.LapSeries()) is None


def test_render_returns_svg_with_fastest() -> None:
    df = pd.concat([_lap_df(1, 5000, lap_ms=100000), _lap_df(2, 5000, lap_ms=99000), _lap_df(3, 200)], ignore_index=True)
    series = tv.clean_laps(df)
    svg = tv.render_telemetry_svg(series)
    assert svg is not None
    assert svg.lstrip().startswith("<?xml") or "<svg" in svg
    assert "</svg>" in svg


def test_render_returns_svg_without_valid_lap() -> None:
    df = pd.concat([_lap_df(1, 5000, invalid=4000), _lap_df(2, 5000, invalid=4000), _lap_df(3, 200)], ignore_index=True)
    series = tv.clean_laps(df)
    svg = tv.render_telemetry_svg(series)
    assert svg is not None and "<svg" in svg


from api.models import Analysis, SessionInfo, Test


def _analysis(session_id="2026-06-19T11:06:54.186Z", driver="Tomas Eviltwin", status="complete") -> Analysis:
    from api.models import AnalysisContext

    return Analysis(
        _id="a1",
        test_id="TST-0001",
        session_id=session_id,
        status=status,
        context=AnalysisContext(driver=driver, track="Spa", car_model="porsche_991ii_gt3_r"),
    )


def _test_with_session(session_id="2026-06-19T11:06:54.186Z") -> Test:
    return Test(
        _id="TST-0001",
        name="t",
        driver="Tomas Eviltwin",
        pc_device_id="DEV-0001",
        test_rig_device_id="DEV-0001",
        environment_id="ENV-0001",
        experiment_id="ConferenceBrno",
        config_id="cfg-001",
        sessions=[SessionInfo(session_id=session_id, track="Spa", car_model="porsche_991ii_gt3_r")],
    )


def test_resolve_lake_keys_lowercases_driver() -> None:
    keys = tv.resolve_lake_keys(_analysis(), _test_with_session())
    assert keys == ("tomas eviltwin", "Spa", "porsche_991ii_gt3_r")


def test_resolve_lake_keys_none_without_session() -> None:
    assert tv.resolve_lake_keys(_analysis(session_id=None), _test_with_session()) is None


def test_build_svg_happy(monkeypatch: pytest.MonkeyPatch) -> None:
    df = pd.concat([_lap_df(1, 5000), _lap_df(2, 5000, lap_ms=99000), _lap_df(3, 200)], ignore_index=True)
    monkeypatch.setattr(tv, "lake_query", lambda sql: df)
    svg = tv.build_analysis_telemetry_svg(_analysis(), _test_with_session(), "ac_telemetry_prod")
    assert svg is not None and "<svg" in svg


def test_build_svg_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(sql):  # noqa: ANN001, ANN202
        raise RuntimeError("no creds")

    monkeypatch.setattr(tv, "lake_query", boom)
    assert tv.build_analysis_telemetry_svg(_analysis(), _test_with_session(), "t") is None


def test_build_svg_none_for_test_wide() -> None:
    assert tv.build_analysis_telemetry_svg(_analysis(session_id=None), _test_with_session(), "t") is None
