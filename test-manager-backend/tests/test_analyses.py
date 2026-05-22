"""Tests for the Analysis Pydantic models and CRUD routes."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from api.models import (
    Analysis,
    AnalysisCreate,
    AnalysisListQuery,
    Anomaly,
    KpiValue,
    RequirementCheck,
    SaveAnalysisPayload,
)


# --- Nested types --------------------------------------------------------- #


def test_kpi_value_minimal():
    k = KpiValue(name="best_lap", value="1:45.321")
    assert k.unit is None
    assert k.notes is None


def test_kpi_value_full():
    k = KpiValue(
        name="top_speed", value=213.4, unit="km/h", notes="lap 9 main straight"
    )
    assert k.value == 213.4
    assert k.unit == "km/h"


def test_requirement_check_tri_state_met():
    assert RequirementCheck(requirement="x", met=True).met is True
    assert RequirementCheck(requirement="x", met=False).met is False
    assert RequirementCheck(requirement="x", met=None).met is None
    assert RequirementCheck(requirement="x").met is None  # default


def test_anomaly_severity_literal():
    a = Anomaly(severity="warn", kind="brake_spike", description="hot brake")
    assert a.severity == "warn"
    with pytest.raises(ValidationError):
        Anomaly(severity="critical", kind="x", description="y")  # ty: ignore[invalid-argument-type]


# --- Analysis main model -------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_analysis_minimal_defaults():
    a = Analysis(
        _id="uuid-abc",
        test_id="TST-1",
        session_id="2026-01-01T00:00:00Z",
        status="pending",
    )
    assert a.id == "uuid-abc"
    assert a.schema_version == 1
    assert a.kpis == []
    assert a.requirements_check == []
    assert a.anomalies == []
    assert a.logbook_refs == []
    assert a.summary_md == ""
    assert a.extra == {}
    assert a.tokens_in is None
    assert a.tokens_cache_read is None
    assert a.error is None


def test_analysis_round_trip_with_alias():
    """Pydantic must accept either `id` or `_id` when populate_by_name=True."""
    a = Analysis(_id="uuid-xyz", test_id="t", session_id="s", status="pending")
    dumped = a.model_dump(by_alias=True)
    assert dumped["_id"] == "uuid-xyz"
    again = Analysis(**dumped)
    assert again.id == "uuid-xyz"


def test_analysis_invalid_status_rejected():
    with pytest.raises(ValidationError):
        Analysis(_id="x", test_id="t", session_id="s", status="bogus")  # ty: ignore[invalid-argument-type]


def test_analysis_invalid_error_kind_rejected():
    with pytest.raises(ValidationError):
        Analysis(
            _id="x",
            test_id="t",
            session_id="s",
            status="failed",
            error_kind="bogus",  # ty: ignore[invalid-argument-type]
        )


# --- Request models ------------------------------------------------------- #


def test_analysis_create_requires_both_ids():
    with pytest.raises(ValidationError):
        AnalysisCreate(test_id="", session_id="s")  # min_length=1 on test_id
    with pytest.raises(ValidationError):
        AnalysisCreate(test_id="t", session_id="")
    ok = AnalysisCreate(test_id="t", session_id="s")
    assert ok.test_id == "t"


def test_save_analysis_payload_requires_summary_md():
    """summary_md is the only required content field — it's the narrative spine."""
    with pytest.raises(ValidationError):
        SaveAnalysisPayload(analysis_id="x", summary_md="")  # min_length=1
    ok = SaveAnalysisPayload(analysis_id="x", summary_md="# ok")
    assert ok.kpis == []  # all other content fields optional, default empty
    assert ok.requirements_check == []
    assert ok.anomalies == []
    assert ok.extra == {}


def test_analysis_list_query_status_literal():
    q = AnalysisListQuery(status="complete")
    assert q.status == "complete"
    with pytest.raises(ValidationError):
        AnalysisListQuery(status="bogus")  # ty: ignore[invalid-argument-type]
