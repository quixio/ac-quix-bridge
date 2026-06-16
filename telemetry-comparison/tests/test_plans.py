"""Pydantic plan validation."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from plans import AgentPlan, ClarifyPlan, PlotPlan

ADAPTER: TypeAdapter[AgentPlan] = TypeAdapter(AgentPlan)


def _trace(**overrides: str | int) -> dict:
    base = {
        "session_id": "2026-04-17T06:39:45.652Z",
        "lap": 1,
        "driver": "ludvik",
        "carModel": "bmw_1m",
        "track": "ks_nurburgring",
        "experiment": "VideoSyncFix",
        "environment": "prague_office",
        "test_rig": "g29",
    }
    base.update(overrides)
    return base


def test_plot_plan_minimal_valid() -> None:
    plan = ADAPTER.validate_python({"type": "plot", "signals": ["speedKmh"], "traces": [_trace()]})
    assert isinstance(plan, PlotPlan)
    assert plan.signals == ["speedKmh"]
    assert plan.traces[0].lap == 1


def test_plot_plan_title_optional() -> None:
    plan = ADAPTER.validate_python({"type": "plot", "signals": ["speedKmh"], "traces": [_trace()]})
    assert isinstance(plan, PlotPlan)
    assert plan.title == ""


def test_plot_plan_rejects_empty_signals() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "plot", "signals": [], "traces": [_trace()]})


def test_plot_plan_rejects_empty_traces() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "plot", "signals": ["speedKmh"], "traces": []})


def test_trace_ignores_extra_fields() -> None:
    """Trace allows agent to grow optional annotation fields without breaking."""
    plan = ADAPTER.validate_python(
        {
            "type": "plot",
            "signals": ["speedKmh"],
            "traces": [_trace(color_hint="#ff0000")],
        }
    )
    assert isinstance(plan, PlotPlan)


def test_clarify_plan_valid() -> None:
    plan = ADAPTER.validate_python(
        {"type": "clarify", "question": "Which driver?", "options": ["a", "b"]}
    )
    assert isinstance(plan, ClarifyPlan)
    assert plan.options == ["a", "b"]


def test_clarify_plan_options_default_empty() -> None:
    plan = ADAPTER.validate_python({"type": "clarify", "question": "Which driver?"})
    assert isinstance(plan, ClarifyPlan)
    assert plan.options == []


def test_discriminator_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        ADAPTER.validate_python({"type": "wat", "signals": ["x"], "traces": [_trace()]})


def test_plot_plan_rejects_extra_fields() -> None:
    """PlotPlan uses extra='forbid' so typos surface immediately."""
    with pytest.raises(ValidationError):
        ADAPTER.validate_python(
            {
                "type": "plot",
                "signals": ["speedKmh"],
                "traces": [_trace()],
                "bogus": "field",
            }
        )
