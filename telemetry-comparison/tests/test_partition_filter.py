"""Unit tests for main._build_partition_filter.

Pure function, no mocks required. Pins the quoting and special-case logic
(CAST+LIKE for session_id) that the SQL-based endpoints rely on.
"""

from __future__ import annotations

from main import _build_partition_filter


def test_empty_input_returns_empty_string() -> None:
    assert _build_partition_filter() == ""


def test_empty_strings_are_skipped() -> None:
    assert _build_partition_filter(environment="", test_rig="") == ""


def test_none_values_are_skipped() -> None:
    assert _build_partition_filter(environment=None, driver="alice") == (
        "WHERE driver = 'alice'"
    )


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


def test_session_id_uses_cast_and_like_prefix() -> None:
    # Hive stores as ISO-Z; frontend may send space-separated with microseconds.
    # Filter must handle both by prefix-matching on a normalized form.
    result = _build_partition_filter(session_id="2026-04-14T11:42:08.107Z")
    assert "CAST(session_id AS VARCHAR) LIKE" in result
    assert "2026-04-14 11:42:08.107" in result


def test_session_id_with_space_format_normalizes() -> None:
    result = _build_partition_filter(session_id="2026-04-14 11:42:08.1070000")
    assert "CAST(session_id AS VARCHAR) LIKE" in result
    # Trailing zeros are stripped so the prefix matches both formats
    assert "2026-04-14 11:42:08.107" in result


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
    assert "CAST(session_id AS VARCHAR) LIKE" in result
    assert result.count(" AND ") == 2
