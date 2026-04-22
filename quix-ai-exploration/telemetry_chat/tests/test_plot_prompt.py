"""Unit tests for plot_prompt.build_first_turn_message + sessions_as_csv."""

from __future__ import annotations

from app.plot_prompt import build_first_turn_message, sessions_as_csv


def test_sessions_as_csv_header_and_rows() -> None:
    sessions: list[dict[str, object]] = [
        {
            "environment": "prague_office",
            "test_rig": "g29",
            "experiment": "BridgeConfigTest",
            "driver": "daniel",
            "track": "ks_nurburgring",
            "carModel": "bmw_1m",
            "session_id": "2026-04-15T10:02:25.041Z",
            "laps": [1, 2, 3],
        },
    ]
    out = sessions_as_csv(sessions)
    lines = out.splitlines()
    assert lines[0].startswith("environment,test_rig,experiment,driver")
    assert "1|2|3" in lines[1]
    assert "daniel" in lines[1]
    assert "2026-04-15T10:02:25.041Z" in lines[1]


def test_sessions_as_csv_quotes_values_with_commas() -> None:
    # Partition values in the future might contain commas (e.g. track names
    # like "magione, short"). csv.writer must wrap those in quotes rather
    # than letting them break column alignment silently.
    sessions: list[dict[str, object]] = [
        {
            "environment": "prague_office",
            "test_rig": "g29",
            "experiment": "corner, late brake",
            "driver": "daniel",
            "track": "ks_nurburgring",
            "carModel": "bmw_1m",
            "session_id": "sid",
            "laps": [1],
        },
    ]
    out = sessions_as_csv(sessions)
    # The experiment value must be quoted so its embedded comma stays inside
    # the column. Downstream LLM will parse it as a single field.
    assert '"corner, late brake"' in out
    # Row must still have the right number of columns (8).
    header, row = out.splitlines()
    assert header.count(",") == 7  # 8 columns → 7 commas
    # Rough check — the comma inside the quoted field is one of the row's
    # commas, but overall column count matches by CSV parse, not by raw
    # comma count. Full parse via csv module:
    import csv
    import io

    parsed = list(csv.reader(io.StringIO(out)))
    assert len(parsed[1]) == 8
    assert parsed[1][2] == "corner, late brake"


def test_first_turn_message_under_portal_cap() -> None:
    # Portal's hard cap is 10,000 bytes. With realistic payloads we should
    # stay well under that. Guard against regressions that would push the
    # combined block back over the line.
    sample: list[dict[str, object]] = [
        {
            "environment": "prague_office",
            "test_rig": "g29",
            "experiment": f"exp_{i}",
            "driver": "daniel",
            "track": "ks_nurburgring",
            "carModel": "bmw_1m",
            "session_id": f"2026-04-15T10:0{i}:00.000Z",
            "laps": [1, 2, 3],
        }
        for i in range(10)
    ]
    msg = build_first_turn_message(user_message="Plot speed", sessions=sample)
    assert len(msg.encode("utf-8")) < 10_000
