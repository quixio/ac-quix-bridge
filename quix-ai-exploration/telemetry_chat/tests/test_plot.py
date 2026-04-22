"""Unit tests for plot.py JSON extraction + validation paths.

Integration (real /api/plot against a live Quix AI + lake) is out of scope
for the unit suite — those belong in a slow/integration marker.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from app.plot import _extract_json, _resolve_plot


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


async def test_resolve_plot_rejects_unknown_signal() -> None:
    parsed = {
        "type": "plot",
        "title": "x",
        "signals": ["not_a_real_channel"],
        "traces": [{"lap": 1, "session_id": "abc"}],
    }
    with pytest.raises(HTTPException) as exc:
        await _resolve_plot("sid", parsed)
    assert exc.value.status_code == 502
    assert "not a known channel" in exc.value.detail


async def test_resolve_plot_strips_unit_bracket_from_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Agent sometimes copies the condensed-prompt display string (with unit
    # bracket) into the signal field. Strip before validating so the known-
    # channel check passes against the bare name.
    async def fake_get_telemetry(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["signals"] == ["speedKmh"], (
            f"expected stripped signal, got {kwargs['signals']!r}"
        )
        return {"data": {"normalizedCarPosition": [], "speedKmh": []}, "count": 0}

    monkeypatch.setattr("app.plot.get_telemetry", fake_get_telemetry)

    parsed = {
        "type": "plot",
        "signals": ["speedKmh[km/h]"],
        "traces": [{"lap": 1, "track": "t", "session_id": "s"}],
    }
    result = await _resolve_plot("sid", parsed)
    assert result["charts"][0]["signal"] == "speedKmh"


async def test_resolve_plot_supports_multiple_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Multi-signal request fans out to N × M lake fetches; result bucketed
    # back into one chart per signal.
    seen_signals: list[str] = []

    async def fake_get_telemetry(**kwargs: Any) -> dict[str, Any]:
        signal = kwargs["signals"][0]
        seen_signals.append(signal)
        return {"data": {"normalizedCarPosition": [0.0], signal: [1.0]}, "count": 1}

    monkeypatch.setattr("app.plot.get_telemetry", fake_get_telemetry)

    parsed = {
        "type": "plot",
        "signals": ["speedKmh", "gas", "brake"],
        "traces": [
            {"lap": 1, "track": "t", "session_id": "s"},
            {"lap": 2, "track": "t", "session_id": "s"},
        ],
    }
    result = await _resolve_plot("sid", parsed)
    assert len(result["charts"]) == 3
    assert [c["signal"] for c in result["charts"]] == ["speedKmh", "gas", "brake"]
    # 3 signals × 2 traces = 6 fetches
    assert len(seen_signals) == 6


async def test_resolve_plot_caps_too_many_signals() -> None:
    # 11 real channels — one above the MAX_SIGNALS = 10 cap.
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
        "traces": [{"lap": 1, "track": "t", "session_id": "s"}],
    }
    with pytest.raises(HTTPException) as exc:
        await _resolve_plot("sid", parsed)
    assert exc.value.status_code == 400
    assert "Too many signals" in exc.value.detail


async def test_resolve_plot_rejects_missing_signal() -> None:
    parsed: dict = {"type": "plot", "title": "x", "traces": [{"lap": 1}]}
    with pytest.raises(HTTPException) as exc:
        await _resolve_plot("sid", parsed)
    assert exc.value.status_code == 502


async def test_resolve_plot_rejects_empty_traces() -> None:
    parsed = {"type": "plot", "signal": "speedKmh", "traces": []}
    with pytest.raises(HTTPException) as exc:
        await _resolve_plot("sid", parsed)
    assert exc.value.status_code == 502


async def test_resolve_plot_caps_too_many_traces() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [{"lap": i, "track": "t", "session_id": "s"} for i in range(10)],
    }
    with pytest.raises(HTTPException) as exc:
        await _resolve_plot("sid", parsed)
    assert exc.value.status_code == 400
    assert "Too many traces" in exc.value.detail


async def test_resolve_plot_rejects_non_int_lap_synchronously() -> None:
    # Pre-validation must raise before fan-out — a non-int lap inside
    # asyncio.gather(return_exceptions=True) would otherwise be swallowed
    # and silently drop the trace.
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [{"lap": "one", "track": "t", "session_id": "s"}],
    }
    with pytest.raises(HTTPException) as exc:
        await _resolve_plot("sid", parsed)
    assert exc.value.status_code == 502
    assert "lap" in exc.value.detail


async def test_resolve_plot_rejects_cross_track() -> None:
    parsed = {
        "type": "plot",
        "signals": ["speedKmh"],
        "traces": [
            {"lap": 1, "track": "monza", "session_id": "s1"},
            {"lap": 1, "track": "spa", "session_id": "s2"},
        ],
    }
    with pytest.raises(HTTPException) as exc:
        await _resolve_plot("sid", parsed)
    assert exc.value.status_code == 400
    assert "multiple tracks" in exc.value.detail
