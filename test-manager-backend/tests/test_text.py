"""Unit tests for the shared text-normalization helpers (api/text.py).

These pin `fold_for_lookup` (extracted from live_telemetry) and the
driver-specific `driver_name_key` so the lake-identity / dedup key can't
silently drift if the leaderboard code that originally owned the fold changes.
"""

from api.text import driver_name_key, fold_for_lookup


def test_fold_lowercases() -> None:
    assert fold_for_lookup("Daniel") == "daniel"


def test_fold_strips_accents() -> None:
    assert fold_for_lookup("Petr Čech") == "petr cech"


def test_fold_empty_string() -> None:
    assert fold_for_lookup("") == ""


def test_fold_all_non_ascii_falls_back_to_lower() -> None:
    # NFKD-stripping an all-CJK name would empty it; fall back to lower().
    assert fold_for_lookup("日本") == "日本".lower()


def test_driver_name_key_collapses_whitespace() -> None:
    assert driver_name_key("Petr   Cech") == driver_name_key("Petr Cech")


def test_driver_name_key_trims() -> None:
    assert driver_name_key("  Daniel Lastic  ") == "daniel lastic"


def test_driver_name_key_accent_and_case_insensitive() -> None:
    assert driver_name_key("Petr Čech") == driver_name_key("petr cech")
