"""Unit tests for partition_filter._build_partition_filter.

Pure function, no mocks required. Pins the quoting and special-case logic
(CAST+LIKE for session_id) that the SQL-based endpoints rely on, plus the
single-quote escaping that neutralizes SQL injection while still allowing
accented / Unicode partition values that legitimately exist in the lake.
"""

from __future__ import annotations

import pytest

from partition_filter import _build_partition_filter


def test_empty_input_returns_empty_string() -> None:
    assert _build_partition_filter() == ""


def test_empty_strings_are_skipped() -> None:
    assert _build_partition_filter(environment="", test_rig="") == ""


def test_none_values_are_skipped() -> None:
    assert _build_partition_filter(environment=None, driver="alice") == ("WHERE driver = 'alice'")


def test_single_partition_column_quotes_string() -> None:
    assert _build_partition_filter(environment="prague_office") == (
        "WHERE environment = 'prague_office'"
    )


def test_multiple_columns_joined_with_and() -> None:
    result = _build_partition_filter(environment="x", test_rig="y")
    assert result == "WHERE environment = 'x' AND test_rig = 'y'"


def test_integer_values_not_quoted() -> None:
    # `lap` is an int partition — should not be wrapped in quotes
    assert _build_partition_filter(lap=3) == "WHERE lap = 3"


def test_session_id_iso_z_matches_stored_form_exactly() -> None:
    # Stored partition path is ISO-8601 with trailing Z. Exact `=` lets the
    # catalog prune to the one session; CAST+LIKE would defeat pruning (~7x).
    assert _build_partition_filter(session_id="2026-04-14T11:42:08.107Z") == (
        "WHERE session_id = '2026-04-14T11:42:08.107Z'"
    )


def test_session_id_display_form_canonicalized() -> None:
    # DuckDB infers the partition as TIMESTAMP and displays it space-separated
    # without the Z. Re-canonicalize to the stored ISO-Z form so it still
    # matches the partition path (and still prunes).
    assert _build_partition_filter(session_id="2026-04-14 11:42:08.107") == (
        "WHERE session_id = '2026-04-14T11:42:08.107Z'"
    )


def test_session_id_missing_z_is_appended() -> None:
    assert _build_partition_filter(session_id="2026-04-14T11:42:08.107") == (
        "WHERE session_id = '2026-04-14T11:42:08.107Z'"
    )


def test_mixed_columns_combine_correctly() -> None:
    result = _build_partition_filter(
        environment="prague_office",
        lap=2,
        session_id="2026-04-14T11:42:08.107Z",
    )
    # All three clauses present, joined with AND
    assert result.startswith("WHERE ")
    assert "environment = 'prague_office'" in result
    assert "lap = 2" in result
    assert "session_id = '2026-04-14T11:42:08.107Z'" in result
    assert result.count(" AND ") == 2


def test_single_quote_escaped_not_rejected() -> None:
    # Apostrophes appear in real names (O'Hara). Escape by doubling instead of
    # rejecting — same as the frontend Lakehouse embed and leaderboard.
    assert _build_partition_filter(environment="o'hara") == ("WHERE environment = 'o''hara'")


def test_sql_injection_neutralized_by_escaping() -> None:
    # Classic payload is accepted but rendered inert: doubled quotes keep it
    # inside the string literal, so it can't break out into a new statement.
    assert _build_partition_filter(driver="' OR '1'='1") == ("WHERE driver = ''' OR ''1''=''1'")


def test_semicolon_inert_inside_string_literal() -> None:
    # A semicolon / DROP inside a quoted literal is just data, not a statement.
    assert (
        _build_partition_filter(environment="prague_office; DROP TABLE ac_telemetry")
        == "WHERE environment = 'prague_office; DROP TABLE ac_telemetry'"
    )


def test_accented_unicode_value_allowed() -> None:
    # Accented partition values (Petr Čech, daniel laštic) are real lake
    # partition keys — must build a query, not raise.
    assert _build_partition_filter(driver="daniel laštic") == ("WHERE driver = 'daniel laštic'")


def test_control_character_rejected() -> None:
    # Control chars (newlines etc.) are never legitimate partition values.
    with pytest.raises(ValueError, match="Invalid character"):
        _build_partition_filter(driver="bad\nname")


def test_benign_special_chars_allowed() -> None:
    # Dashes, dots, colons, spaces are all in real partition values
    # (session_id timestamps). Must not be rejected.
    result = _build_partition_filter(
        environment="prague_office",
        driver="Daniel-Lastic",
        session_id="2026-04-14T11:42:08.107Z",
    )
    assert "prague_office" in result
    assert "Daniel-Lastic" in result
