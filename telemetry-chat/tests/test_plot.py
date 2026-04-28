"""Unit tests for plot.py JSON extraction + plan validation.

The streaming generator `_plot_events` is covered by end-to-end tests
against a live Quix AI + lake (out of scope for the unit suite); here we
test the synchronous validation helpers it delegates to.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.plot import _extract_json, _plan


def _trace(**overrides: Any) -> dict[str, Any]:
    """Full 8-field trace dict — overrides only the fields under test."""
    base = {
        "session_id": "s",
        "lap": 1,
        "driver": "d",
        "carModel": "c",
        "track": "t",
        "experiment": "e",
        "environment": "p",
        "test_rig": "r",
    }
    return {**base, **overrides}


def test_extract_json_picks_last_fenced_block() -> None:
    reply = 'Thinking out loud...\n\n```json\n{"type": "clarify", "question": "x", "options": []}\n```\n'
    parsed = _extract_json(reply)
    assert parsed["type"] == "clarify"
    assert parsed["question"] == "x"


def test_extract_json_handles_multiple_blocks() -> None:
    reply = (
        "First draft:\n"
        '```json\n{"type": "clarify"}\n```\n'
        "Final answer:\n"
        '```json\n{"type": "plot", "signal": "speedKmh"}\n```\n'
    )
    parsed = _extract_json(reply)
    assert parsed["type"] == "plot"
    assert parsed["signal"] == "speedKmh"


def test_extract_json_raises_on_missing_fence() -> None:
    with pytest.raises(HTTPException) as exc:
        _extract_json("no json here at all")
    assert exc.value.status_code == 502
    assert "json" in exc.value.detail.lower()


def test_extract_json_raises_on_malformed_json() -> None:
    reply = '```json\n{"type": "plot",\n```\n'
    with pytest.raises(HTTPException) as exc:
        _extract_json(reply)
    assert exc.value.status_code == 502


def test_extract_json_raises_when_not_object() -> None:
    reply = '```json\n["not", "an", "object"]\n```\n'
    with pytest.raises(HTTPException) as exc:
        _extract_json(reply)
    assert exc.value.status_code == 502


def test_plan_rejects_unknown_signal() -> None:
    parsed = {
        "type": "plot",
        "title": "x",
        "signals": ["not_a_real_channel"],
        "traces": [_trace()],
    }
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502
    assert "not a known channel" in exc.value.detail


def test_plan_strips_unit_bracket_from_signal() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh[km/h]"],
        "traces": [_trace()],
    }
    plan = _plan(parsed)
    assert plan["signals"] == ["speedKmh"]


def test_plan_accepts_multiple_signals() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh", "gas", "brake"],
        "traces": [_trace(lap=1), _trace(lap=2)],
    }
    plan = _plan(parsed)
    assert plan["signals"] == ["speedKmh", "gas", "brake"]
    assert len(plan["traces"]) == 2
    assert plan["track"] == "t"


def test_plan_rejects_missing_signals() -> None:
    parsed: dict = {"type": "plot", "title": "x", "traces": [_trace()]}
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502


def test_plan_rejects_empty_traces() -> None:
    parsed = {"type": "plot", "signals": ["speedKmh"], "traces": []}
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502


def test_plan_caps_too_many_traces() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [_trace(lap=i) for i in range(10)],
    }
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 400
    assert "Too many traces" in exc.value.detail


def test_plan_caps_too_many_signals() -> None:
    parsed = {
        "type": "plot",
        "signals": [
            "speedKmh",
            "gas",
            "brake",
            "rpms",
            "clutch",
            "gear",
            "steerAngle",
            "fuel",
            "engineBrake",
            "turboBoost",
            "heading",
        ],
        "traces": [_trace()],
    }
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 400
    assert "Too many signals" in exc.value.detail


def test_plan_rejects_non_int_lap() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [_trace(lap="one")],
    }
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502
    assert "lap" in exc.value.detail.lower()


def test_plan_rejects_cross_track() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [_trace(track="monza"), _trace(track="spa")],
    }
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 400
    assert "multiple tracks" in exc.value.detail


def test_plan_rejects_unknown_type() -> None:
    parsed = {"type": "weird", "signals": ["speedKmh"], "traces": [_trace()]}
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502


def test_plan_rejects_clarify_through_plot_path() -> None:
    """`_plan` is only called for plot type — clarify reaching it is a bug."""
    parsed = {"type": "clarify", "question": "?", "options": []}
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502


def test_plan_ignores_extra_fields_on_trace() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [_trace(color_hint="red")],
    }
    plan = _plan(parsed)
    assert "color_hint" not in plan["traces"][0]


def test_plan_rejects_empty_signals_list() -> None:
    parsed = {"type": "plot", "signals": [], "traces": [_trace()]}
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502


def test_plan_rejects_extra_fields_at_top_level() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [_trace()],
        "weird_extra": 1,
    }
    with pytest.raises(HTTPException) as exc:
        _plan(parsed)
    assert exc.value.status_code == 502
