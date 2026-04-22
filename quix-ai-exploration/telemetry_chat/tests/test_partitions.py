"""WHERE-builder edge cases. Mirrors telemetry-comparison/tests/test_partition_filter.py,
scoped to the subset we actually use (we don't own track/video paths here)."""

import pytest

from app.partitions import build_partition_filter


def test_empty_returns_empty_string() -> None:
    assert build_partition_filter() == ""
    assert build_partition_filter(driver="", track="") == ""


def test_single_string_filter() -> None:
    assert build_partition_filter(driver="daniel") == "WHERE driver = 'daniel'"


def test_integer_filter_no_quotes() -> None:
    assert build_partition_filter(lap=3) == "WHERE lap = 3"


def test_multiple_filters_joined_with_and() -> None:
    result = build_partition_filter(driver="daniel", carModel="bmw_1m")
    # Order of kwargs is preserved in Python 3.7+, so we can assert verbatim.
    assert result == "WHERE driver = 'daniel' AND carModel = 'bmw_1m'"


def test_session_id_uses_cast_and_like() -> None:
    result = build_partition_filter(session_id="2026-04-15T10:02:25.041Z")
    # Value normalises: T→' ', strip Z, strip trailing zeros + dot.
    assert "CAST(session_id AS VARCHAR) LIKE '2026-04-15 10:02:25.041%'" in result
    assert "ESCAPE '\\'" in result


def test_rejects_sql_injection_attempt() -> None:
    with pytest.raises(ValueError, match="Invalid character"):
        build_partition_filter(driver="daniel'; DROP TABLE --")


def test_single_quote_escape_defense_in_depth() -> None:
    # The regex allow-list rejects `'`, but we double any surviving quote
    # too — verify via a monkeypatched allow-list (the live one blocks it).
    import re

    from app import partitions

    original = partitions._SAFE_PARTITION_VALUE
    partitions._SAFE_PARTITION_VALUE = re.compile(r".+")  # temporarily permissive
    try:
        out = build_partition_filter(driver="O'Brien")
        assert out == "WHERE driver = 'O''Brien'"
    finally:
        partitions._SAFE_PARTITION_VALUE = original


def test_rejects_semicolon() -> None:
    with pytest.raises(ValueError):
        build_partition_filter(driver="a;b")


def test_allows_dot_dash_colon_space() -> None:
    # These are legitimate in session_id timestamps; must not raise.
    assert build_partition_filter(session_id="2026-04-15T10:02:25.041Z").startswith(
        "WHERE CAST(session_id"
    )


def test_session_id_like_special_chars_escaped() -> None:
    # Underscores are valid in carModel etc.; in session_id they'd widen
    # the LIKE match so must be escaped.
    result = build_partition_filter(session_id="abc_def")
    assert "LIKE 'abc\\_def%'" in result
