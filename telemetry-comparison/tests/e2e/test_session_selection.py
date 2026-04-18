"""E2E: tab-open and session selection flows.

These run against the real dev server + real QuixLake. They validate what
the user sees, not just the backend contract.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e

PART_COLS = [
    "environment",
    "test_rig",
    "experiment",
    "driver",
    "track",
    "carModel",
    "session_id",
]


def _dropdown_values(page: Page, row_idx: int = 0) -> dict[str, str]:
    """Read the currently-selected value of every partition dropdown in the
    first selection row. Useful for asserting the state after cascading."""
    result: dict[str, str] = {}
    for col in PART_COLS:
        sel = page.locator(f"#{col}-{row_idx}")
        if sel.count():
            result[col] = sel.evaluate("el => el.value")
    return result


def test_direct_access_autoselects_first_at_each_level(page: Page, base_url: str) -> None:
    page.goto(base_url)
    # The initial placeholder row has disabled selects; wait for the real
    # first row (row-0) to appear. Its selects are live and enabled.
    page.wait_for_selector("#environment-0:not([disabled])", timeout=15000)

    selections = _dropdown_values(page)
    # All 7 partition cols should have a non-empty selection once the
    # cascade finishes. (Exact values depend on lake contents.)
    for col in PART_COLS:
        assert selections.get(col), f"expected dropdown {col} to be populated, got: {selections}"

    # Status bar should mention how many sessions loaded.
    expect(page.locator("#status")).to_contain_text("sessions loaded")


def test_deep_link_with_valid_params_pre_selects_them(page: Page, base_url: str) -> None:
    params = {
        "environment": "prague_office",
        "test_rig": "g29",
        "experiment": "VideoStartSeek",
        "driver": "ludvik",
        "track": "ks_nurburgring",
        "carModel": "bmw_1m",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    page.goto(f"{base_url}/?{qs}")
    page.wait_for_selector("#environment-0:not([disabled])", timeout=15000)

    selections = _dropdown_values(page)
    for col, expected in params.items():
        assert selections[col] == expected, f"{col}: expected {expected}, got {selections[col]}"


def test_deep_link_with_bogus_params_shows_toast(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/?environment=does_not_exist&test_rig=nope")
    # Toast should show up shortly after the fast-path fetch returns empty.
    toast = page.locator(".toast--warn")
    expect(toast).to_be_visible(timeout=10000)
    expect(toast).to_contain_text("No sessions match")
    expect(toast).to_contain_text("environment=does_not_exist")

    # UI should still be usable — background full load kicks in and the
    # row gets real dropdown values (not empty).
    page.wait_for_selector("#environment-0:not([disabled])", timeout=15000)
    selections = _dropdown_values(page)
    assert selections["environment"], "fallback cascade should auto-select a real environment"
    assert selections["environment"] != "does_not_exist"


def test_changing_upstream_dropdown_updates_downstream(page: Page, base_url: str) -> None:
    """Picking a different value at level N should re-cascade levels N+1..end."""
    page.goto(base_url)
    page.wait_for_selector("#environment-0:not([disabled])", timeout=15000)

    # Capture the starting session_id value.
    before = _dropdown_values(page)

    # Pick a non-default experiment if the lake has more than one.
    exp_sel = page.locator("#experiment-0")
    options = exp_sel.locator("option").all_inner_texts()
    # Filter to options with a value (exclude "...") and not the current one.
    current_exp = before["experiment"]
    alternatives = [o for o in options if o and o != current_exp and o != "..."]
    if not alternatives:
        pytest.skip("lake has only one experiment — can't test cascade re-populate")

    exp_sel.select_option(alternatives[0])
    # Trigger the change event programmatically — Playwright select_option
    # should already dispatch it, but it's defensive.
    exp_sel.evaluate("el => el.dispatchEvent(new Event('change'))")

    # Wait for the cascade to settle. Downstream dropdowns should have new
    # values (may or may not differ from before, depending on the lake).
    page.wait_for_timeout(500)
    after = _dropdown_values(page)
    assert after["experiment"] == alternatives[0]
    # session_id for the new experiment should be populated (not empty).
    assert after["session_id"], "session_id must cascade to a real value"
