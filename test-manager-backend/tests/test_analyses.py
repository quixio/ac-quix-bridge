"""Tests for the Analysis Pydantic models and CRUD routes."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from api.models import (
    Analysis,
    AnalysisContext,
    AnalysisCreate,
    AnalysisListQuery,
    Anomaly,
    KpiValue,
    RequirementCheck,
    SaveAnalysisPayload,
)
from api.routes.analyses import _build_analysis_context
from tests.conftest import TestFactory


# --- _build_analysis_context (pure; no DB) --------------------------------- #

_TEST_DOC = {
    "driver": "Daniel",
    "sessions": [
        {"session_id": "S1", "track": "Spa", "car_model": "porsche_991ii_gt3_r"},
        {"session_id": "S2", "track": "Zandvoort", "car_model": "ferrari_488_gt3"},
    ],
}


def test_build_context_session_match() -> None:
    ctx = _build_analysis_context(_TEST_DOC, "S1")
    assert ctx == AnalysisContext(
        driver="Daniel", track="Spa", car_model="porsche_991ii_gt3_r"
    )


def test_build_context_test_wide_driver_only() -> None:
    ctx = _build_analysis_context(_TEST_DOC, None)
    assert ctx is not None
    assert ctx.driver == "Daniel"
    assert ctx.track is None and ctx.car_model is None


def test_build_context_unmatched_session_driver_only() -> None:
    ctx = _build_analysis_context(_TEST_DOC, "NOPE")
    assert ctx is not None
    assert ctx.driver == "Daniel"
    assert ctx.track is None


def test_build_context_missing_test_returns_none() -> None:
    assert _build_analysis_context(None, "S1") is None


def test_build_context_empty_test_returns_none() -> None:
    assert _build_analysis_context({}, "S1") is None


def _create_test_with_session(
    client: TestClient,
    create_test: TestFactory,
    session_id: str = "2026-05-22T10:30:00",
) -> tuple[str, str]:
    """Create a test and attach one session to it. Returns (test_id, session_id)."""
    _, created = create_test()
    test_id = created["test_id"]
    response = client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={
            "session_id": session_id,
            "track": "ks_nurburgring",
            "car_model": "bmw_1m",
        },
    )
    assert response.status_code == 200
    return test_id, session_id


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


def test_kpi_and_anomaly_session_id_optional():
    """v2 attribution: KpiValue and Anomaly carry optional session_id."""
    sid = "2026-06-01T13:13:12.038Z"

    k_with = KpiValue(name="best_lap_p32", value=108.2, session_id=sid)
    assert k_with.session_id == sid
    k_default = KpiValue(name="x", value=1.0)
    assert k_default.session_id is None

    a_with = Anomaly(
        severity="warn", kind="tire_overheat", description="FL >100C", session_id=sid
    )
    assert a_with.session_id == sid
    a_default = Anomaly(severity="info", kind="x", description="y")
    assert a_default.session_id is None


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
    assert a.schema_version == 2
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


def test_analysis_create_requires_test_id_or_session_id():
    """F3: test_id became optional — at least one of test_id / session_id required."""
    with pytest.raises(ValidationError):
        AnalysisCreate()  # neither provided
    assert AnalysisCreate(test_id="t").session_id is None
    assert AnalysisCreate(session_id="s").test_id is None
    both = AnalysisCreate(test_id="t", session_id="s")
    assert both.test_id == "t"
    assert both.session_id == "s"


def test_auto_triggered_requires_session_id():
    """Auto trigger must name a session — a null session_id would let the dedup
    match test-wide rows and is never what the trigger sends."""
    with pytest.raises(ValidationError):
        AnalysisCreate(test_id="t", triggered_by="auto")  # no session_id
    ok = AnalysisCreate(session_id="s", triggered_by="auto")
    assert ok.session_id == "s"


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


def test_analysis_list_query_session_id_is_null_field():
    """v2: AnalysisListQuery exposes session_id_is_null filter (default None)."""
    q_default = AnalysisListQuery()
    assert q_default.session_id_is_null is None

    q_true = AnalysisListQuery(session_id_is_null=True)
    assert q_true.session_id_is_null is True

    q_false = AnalysisListQuery(session_id_is_null=False)
    assert q_false.session_id_is_null is False


# --- Routes: POST /api/v1/analyses ---------------------------------------- #


def test_post_analysis_creates_pending_doc_and_returns_202(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id, session_id = _create_test_with_session(client, create_test)

    response = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": session_id},
    )
    assert response.status_code == 202
    body = response.json()
    assert "analysis_id" in body

    # Verify by fetching via GET — tests behaviour rather than Mongo internals.
    detail = client.get(f"/api/v1/analyses/{body['analysis_id']}")
    assert detail.status_code == 200
    doc = detail.json()
    assert doc["status"] == "pending"
    assert doc["test_id"] == test_id
    assert doc["session_id"] == session_id
    assert doc["summary_md"] == ""
    assert doc["kpis"] == []


def test_post_analysis_rejects_unknown_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    _, created = create_test()
    test_id = created["test_id"]
    response = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": "2099-01-01T00:00:00Z"},
    )
    assert response.status_code == 400


def test_post_analysis_accepts_null_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """POST /api/v1/analyses with session_id=None returns 202 (test-wide mode)."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": None},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert "analysis_id" in body

    detail = client.get(f"/api/v1/analyses/{body['analysis_id']}")
    assert detail.status_code == 200
    doc = detail.json()
    assert doc["session_id"] is None
    assert doc["test_id"] == test_id
    assert doc["status"] == "pending"


def test_post_analysis_rejects_unknown_test(client: TestClient) -> None:
    response = client.post(
        "/api/v1/analyses",
        json={"test_id": "TST-9999", "session_id": "2026-01-01T00:00:00Z"},
    )
    assert response.status_code == 404


def test_post_analysis_spawns_runner_when_quix_ai_configured(
    client: TestClient,
    create_test: TestFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Quix.AI env is set, endpoint must schedule BatchAnalysisAI.run on the
    event loop and return 202 — not crash with RuntimeError: no running event loop."""
    monkeypatch.setenv("Quix__Portal__Api", "https://portal.example")
    monkeypatch.setenv("POST_RACE_AGENT_ID", "agent-xyz")

    called = {"ran": False}

    async def _fake_run(self, **kwargs):
        called["ran"] = True

    monkeypatch.setattr(
        "shared.post_race_ai.runner.BatchAnalysisAI.run", _fake_run, raising=True
    )

    test_id, session_id = _create_test_with_session(client, create_test)
    response = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": session_id},
    )
    assert response.status_code == 202, response.text


# --- Routes: triggered_by + per-request token forwarding (F6) -------------- #


def _enable_quix_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Quix__Portal__Api", "https://portal.example")
    monkeypatch.setenv("POST_RACE_AGENT_ID", "agent-xyz")


def _capture_runner_token(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch BatchAnalysisAI so the runner never really runs; capture the
    quix_token the endpoint constructs it with."""
    captured: dict[str, object] = {}

    def _init(self, mongo, **kwargs):  # type: ignore[no-untyped-def]
        captured["quix_token"] = kwargs.get("quix_token")
        self._mongo = mongo

    async def _run(self, **kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(
        "shared.post_race_ai.runner.BatchAnalysisAI.__init__", _init, raising=True
    )
    monkeypatch.setattr(
        "shared.post_race_ai.runner.BatchAnalysisAI.run", _run, raising=True
    )
    return captured


def test_manual_forwards_user_bearer(
    client: TestClient, create_test: TestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manual analyze (default) forwards the caller's bearer to the runner."""
    _enable_quix_ai(monkeypatch)
    captured = _capture_runner_token(monkeypatch)
    test_id, session_id = _create_test_with_session(client, create_test)

    resp = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": session_id},
        headers={"Authorization": "Bearer user-tok"},
    )
    assert resp.status_code == 202, resp.text
    assert captured["quix_token"] == "user-tok"


def test_auto_ignores_bearer_uses_pat_fallback(
    client: TestClient, create_test: TestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-triggered analyze ignores the request bearer (the trigger's service
    token) so the runner falls back to PAT_TOKEN."""
    _enable_quix_ai(monkeypatch)
    captured = _capture_runner_token(monkeypatch)
    test_id, session_id = _create_test_with_session(client, create_test)

    resp = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": session_id, "triggered_by": "auto"},
        headers={"Authorization": "Bearer service-tok"},
    )
    assert resp.status_code == 202, resp.text
    assert captured["quix_token"] is None


def test_manual_without_bearer_falls_back(
    client: TestClient, create_test: TestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_quix_ai(monkeypatch)
    captured = _capture_runner_token(monkeypatch)
    test_id, session_id = _create_test_with_session(client, create_test)

    resp = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": session_id},
    )
    assert resp.status_code == 202, resp.text
    assert captured["quix_token"] is None


def test_triggered_by_defaults_manual_and_roundtrips(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id, session_id = _create_test_with_session(client, create_test)
    created = client.post(
        "/api/v1/analyses", json={"test_id": test_id, "session_id": session_id}
    ).json()

    doc = client.get(f"/api/v1/analyses/{created['analysis_id']}").json()
    assert doc["triggered_by"] == "manual"


def test_triggered_by_invalid_rejected(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id, session_id = _create_test_with_session(client, create_test)
    resp = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": session_id, "triggered_by": "bogus"},
    )
    assert resp.status_code == 422


# --- Routes: F3 auto trigger-secret gate ---------------------------------- #


def test_auto_requires_trigger_secret_when_configured(
    client: TestClient, create_test: TestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When TRIGGER_SECRET is set, an auto POST without the matching header is 403."""
    monkeypatch.setenv("TRIGGER_SECRET", "s3cr3t")
    _, session_id = _create_test_with_session(client, create_test)

    missing = client.post(
        "/api/v1/analyses",
        json={"session_id": session_id, "triggered_by": "auto"},
    )
    assert missing.status_code == 403, missing.text

    wrong = client.post(
        "/api/v1/analyses",
        json={"session_id": session_id, "triggered_by": "auto"},
        headers={"X-Trigger-Secret": "nope"},
    )
    assert wrong.status_code == 403, wrong.text

    ok = client.post(
        "/api/v1/analyses",
        json={"session_id": session_id, "triggered_by": "auto"},
        headers={"X-Trigger-Secret": "s3cr3t"},
    )
    assert ok.status_code == 202, ok.text


def test_manual_unaffected_by_trigger_secret(
    client: TestClient, create_test: TestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate applies only to auto — manual never needs the secret."""
    monkeypatch.setenv("TRIGGER_SECRET", "s3cr3t")
    test_id, session_id = _create_test_with_session(client, create_test)

    resp = client.post(
        "/api/v1/analyses", json={"test_id": test_id, "session_id": session_id}
    )
    assert resp.status_code == 202, resp.text


def test_auto_no_secret_required_when_unset(
    client: TestClient, create_test: TestFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With TRIGGER_SECRET unset (local dev / tests), auto works without a header."""
    monkeypatch.delenv("TRIGGER_SECRET", raising=False)
    _, session_id = _create_test_with_session(client, create_test)

    resp = client.post(
        "/api/v1/analyses", json={"session_id": session_id, "triggered_by": "auto"}
    )
    assert resp.status_code == 202, resp.text


# --- Routes: F3 session-only resolve + auto dedup ------------------------- #


def _insert_analysis_for(
    test_id: str,
    session_id: str | None,
    status: str,
    analysis_id: str,
    created_at: datetime | None = None,
) -> str:
    """Insert an analysis doc bound to a specific (test, session) straight into Mongo."""
    from api.mongo import get_mongo

    ts = created_at or datetime.now(timezone.utc)
    doc = Analysis(
        _id=analysis_id,
        test_id=test_id,
        session_id=session_id,
        status=status,  # type: ignore[arg-type]
        summary_md="x",
        created_at=ts,
        updated_at=ts,
    )
    get_mongo().analyses.insert_one(doc.model_dump(by_alias=True))
    return analysis_id


def test_post_analysis_session_only_resolves_test_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """A session_id-only POST resolves the owning test (the auto-trigger path)."""
    test_id, session_id = _create_test_with_session(client, create_test)

    resp = client.post(
        "/api/v1/analyses",
        json={"session_id": session_id, "triggered_by": "auto"},
    )
    assert resp.status_code == 202, resp.text
    analysis_id = resp.json()["analysis_id"]

    doc = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert doc["test_id"] == test_id
    assert doc["session_id"] == session_id
    assert doc["triggered_by"] == "auto"


def test_post_analysis_session_only_404_when_no_test_owns_session(
    client: TestClient,
) -> None:
    resp = client.post(
        "/api/v1/analyses",
        json={"session_id": "2099-12-31T00:00:00Z", "triggered_by": "auto"},
    )
    assert resp.status_code == 404


def test_post_analysis_requires_test_id_or_session_id(client: TestClient) -> None:
    resp = client.post("/api/v1/analyses", json={"triggered_by": "auto"})
    assert resp.status_code == 422


def test_auto_dedup_returns_existing_with_200(
    client: TestClient, create_test: TestFactory
) -> None:
    """Re-fired auto triggers for the same session return the same analysis (200)."""
    _, session_id = _create_test_with_session(client, create_test)

    first = client.post(
        "/api/v1/analyses", json={"session_id": session_id, "triggered_by": "auto"}
    )
    assert first.status_code == 202, first.text
    first_id = first.json()["analysis_id"]

    second = client.post(
        "/api/v1/analyses", json={"session_id": session_id, "triggered_by": "auto"}
    )
    assert second.status_code == 200, second.text
    assert second.json()["analysis_id"] == first_id


def test_manual_dedups_while_in_progress(
    client: TestClient, create_test: TestFactory
) -> None:
    """A second manual click while one is already running returns the existing
    in-progress analysis (200), not a duplicate run — covers multi-user/tab."""
    test_id, session_id = _create_test_with_session(client, create_test)

    first = client.post(
        "/api/v1/analyses", json={"test_id": test_id, "session_id": session_id}
    )
    assert first.status_code == 202, first.text
    first_id = first.json()["analysis_id"]

    second = client.post(
        "/api/v1/analyses", json={"test_id": test_id, "session_id": session_id}
    )
    assert second.status_code == 200, second.text
    assert second.json()["analysis_id"] == first_id


def test_manual_creates_fresh_after_complete(
    client: TestClient, create_test: TestFactory
) -> None:
    """A human can re-run once the previous analysis has finished (not in
    progress): a completed run must NOT block a fresh manual analysis."""
    test_id, session_id = _create_test_with_session(client, create_test)
    _insert_analysis_for(test_id, session_id, "complete", "a-done-1")

    resp = client.post(
        "/api/v1/analyses", json={"test_id": test_id, "session_id": session_id}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["analysis_id"] != "a-done-1"


def test_manual_ignores_failed(client: TestClient, create_test: TestFactory) -> None:
    """A prior failed analysis must NOT block a fresh manual run."""
    test_id, session_id = _create_test_with_session(client, create_test)
    _insert_analysis_for(test_id, session_id, "failed", "a-failed-m")

    resp = client.post(
        "/api/v1/analyses", json={"test_id": test_id, "session_id": session_id}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["analysis_id"] != "a-failed-m"


def test_dedup_per_target_session_vs_test_wide_independent(
    client: TestClient, create_test: TestFactory
) -> None:
    """An in-progress per-session run must not block a test-wide run (and vice
    versa) — dedup keys on (test_id, session_id) with session_id=None for
    test-wide."""
    test_id, session_id = _create_test_with_session(client, create_test)
    _insert_analysis_for(test_id, session_id, "running", "a-session-run")

    # Test-wide (session_id=None) is a different target → fresh, not deduped.
    test_wide = client.post("/api/v1/analyses", json={"test_id": test_id})
    assert test_wide.status_code == 202, test_wide.text
    assert test_wide.json()["analysis_id"] != "a-session-run"

    # A second test-wide click now dedups against the one we just started.
    again = client.post("/api/v1/analyses", json={"test_id": test_id})
    assert again.status_code == 200, again.text
    assert again.json()["analysis_id"] == test_wide.json()["analysis_id"]


def test_auto_dedup_ignores_failed(
    client: TestClient, create_test: TestFactory
) -> None:
    """A prior failed analysis must NOT block a fresh auto run."""
    test_id, session_id = _create_test_with_session(client, create_test)
    _insert_analysis_for(test_id, session_id, "failed", "a-failed-1")

    resp = client.post(
        "/api/v1/analyses", json={"session_id": session_id, "triggered_by": "auto"}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["analysis_id"] != "a-failed-1"


def test_dedup_ignores_stale_in_progress(
    client: TestClient, create_test: TestFactory
) -> None:
    """A stuck/orphaned in-progress analysis (older than the hard timeout) must
    NOT block a fresh run — otherwise the button greys forever and no new
    analysis can ever start for that target."""
    test_id, session_id = _create_test_with_session(client, create_test)
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    _insert_analysis_for(test_id, session_id, "running", "a-stale", created_at=stale)

    resp = client.post(
        "/api/v1/analyses", json={"test_id": test_id, "session_id": session_id}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["analysis_id"] != "a-stale"


def test_auto_dedup_returns_existing_complete(
    client: TestClient, create_test: TestFactory
) -> None:
    """Auto dedups a *completed* run too (the test-completed event re-fires) —
    unlike manual, which may re-run a finished analysis."""
    test_id, session_id = _create_test_with_session(client, create_test)
    _insert_analysis_for(test_id, session_id, "complete", "a-auto-done")

    resp = client.post(
        "/api/v1/analyses", json={"session_id": session_id, "triggered_by": "auto"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["analysis_id"] == "a-auto-done"


# --- Routes: GET /api/v1/analyses/{id}/pdf (F2) --------------------------- #


def _insert_analysis(status: str = "complete", analysis_id: str = "a-pdf-1") -> str:
    """Insert an analysis doc straight into Mongo and return its id."""
    from api.mongo import get_mongo

    now = datetime.now(timezone.utc)
    doc = Analysis(
        _id=analysis_id,
        test_id="TST-0001",
        session_id="2026-01-01T00:00:00Z",
        status=status,  # type: ignore[arg-type]
        summary_md="## Summary\n\npace ok",
        created_at=now,
        updated_at=now,
    )
    get_mongo().analyses.insert_one(doc.model_dump(by_alias=True))
    return analysis_id


@pytest.mark.requires_weasyprint
def test_pdf_endpoint_returns_pdf_for_complete(client: TestClient) -> None:
    analysis_id = _insert_analysis(status="complete")
    resp = client.get(f"/api/v1/analyses/{analysis_id}/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:4] == b"%PDF"


def test_pdf_endpoint_409_when_incomplete(client: TestClient) -> None:
    analysis_id = _insert_analysis(status="pending", analysis_id="a-pdf-2")
    resp = client.get(f"/api/v1/analyses/{analysis_id}/pdf")
    assert resp.status_code == 409


def test_pdf_endpoint_404_when_missing(client: TestClient) -> None:
    resp = client.get("/api/v1/analyses/does-not-exist/pdf")
    assert resp.status_code == 404


# --- Routes: GET /api/v1/analyses/{id} ------------------------------------ #


def test_get_analysis_by_id(client: TestClient, create_test: TestFactory) -> None:
    test_id, session_id = _create_test_with_session(client, create_test)

    created = client.post(
        "/api/v1/analyses",
        json={"test_id": test_id, "session_id": session_id},
    ).json()
    analysis_id = created["analysis_id"]

    response = client.get(f"/api/v1/analyses/{analysis_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == analysis_id
    assert body["status"] == "pending"


def test_get_analysis_unknown_id_404(client: TestClient) -> None:
    response = client.get("/api/v1/analyses/nonexistent-uuid")
    assert response.status_code == 404


# --- Routes: GET /api/v1/analyses (list) ---------------------------------- #


def test_list_analyses_filters_by_test_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """Two tests, one analysis each. Filter by test_id returns only matching one."""
    t1, s1 = _create_test_with_session(
        client, create_test, session_id="2026-05-22T10:30:00"
    )
    t2, s2 = _create_test_with_session(
        client, create_test, session_id="2026-05-22T11:00:00"
    )

    client.post("/api/v1/analyses", json={"test_id": t1, "session_id": s1})
    client.post("/api/v1/analyses", json={"test_id": t2, "session_id": s2})

    response = client.get(f"/api/v1/analyses?test_id={t1}")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["test_id"] == t1


def test_list_analyses_sorted_desc_by_created_at(
    client: TestClient, create_test: TestFactory
) -> None:
    # Seed directly (distinct created_at) — same-target POSTs now dedup, so we
    # can't create two in-progress runs for one (test, session) via the API.
    t, s = _create_test_with_session(client, create_test)
    older = datetime(2026, 5, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 5, 2, tzinfo=timezone.utc)
    first = _insert_analysis_for(t, s, "complete", "a-older", created_at=older)
    second = _insert_analysis_for(t, s, "complete", "a-newer", created_at=newer)

    response = client.get(f"/api/v1/analyses?test_id={t}")
    items = response.json()["items"]
    assert items[0]["id"] == second
    assert items[1]["id"] == first


def test_list_analyses_pagination(client: TestClient, create_test: TestFactory) -> None:
    t, s = _create_test_with_session(client, create_test)

    for i in range(5):
        _insert_analysis_for(t, s, "complete", f"a-page-{i}")

    response = client.get(f"/api/v1/analyses?test_id={t}&page=1&page_size=2")
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["page"] == 1


def test_list_analyses_rejects_invalid_status(client: TestClient) -> None:
    response = client.get("/api/v1/analyses?status=bogus")
    assert response.status_code == 422


def test_list_filter_by_session_id_is_null(
    client: TestClient, create_test: TestFactory
) -> None:
    """GET /api/v1/analyses?session_id_is_null=true returns only test-wide docs."""
    t, s = _create_test_with_session(client, create_test)

    # One session-mode analysis (session_id set).
    client.post("/api/v1/analyses", json={"test_id": t, "session_id": s})
    # One test-wide analysis (session_id=None).
    client.post("/api/v1/analyses", json={"test_id": t, "session_id": None})

    response = client.get(f"/api/v1/analyses?test_id={t}&session_id_is_null=true")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["session_id"] is None


def test_analysis_create_accepts_null_session_id() -> None:
    """AnalysisCreate accepts session_id=None for test-wide analysis mode.

    v2 schema introduces optional session_id — null means analyze every session
    on the test. Route-level handling of None is covered in Task B1.
    """
    ok = AnalysisCreate(test_id="t", session_id=None)
    assert ok.test_id == "t"
    assert ok.session_id is None

    # Implicit None via omission also works.
    ok2 = AnalysisCreate(test_id="t")
    assert ok2.session_id is None


def test_analysis_allows_null_session_id_and_bumps_schema_version() -> None:
    """Analysis persisted doc tolerates null session_id; schema_version is 2."""
    a = Analysis(_id="uuid-twa", test_id="t", session_id=None, status="pending")
    assert a.session_id is None
    assert a.schema_version == 2


@pytest.mark.requires_weasyprint
def test_pdf_includes_telemetry(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.routes import analyses as analyses_route

    calls = []
    def _fake_build(a, t, table):
        calls.append((a, t, table))
        return "<svg width='10' height='10'></svg>"
    monkeypatch.setattr(analyses_route, "build_analysis_telemetry_svg", _fake_build)
    analysis_id = _insert_analysis(status="complete")
    from api.mongo import get_mongo

    get_mongo().tests.insert_one({
        "_id": "TST-0001",
        "name": "t",
        "driver": "d",
        "test_rig_device_id": "DEV-0001",
        "environment_id": "ENV-0001",
        "experiment_id": "x",
        "pc_device_id": "DEV-0002",
        "config_id": "cfg-0001",
        "sessions": [],
    })
    resp = client.get(f"/api/v1/analyses/{analysis_id}/pdf")
    assert resp.status_code == 200, resp.text
    assert resp.content[:4] == b"%PDF"
    assert len(calls) == 1


# --- Routes: GET /api/v1/analyses/{id}/telemetry (Task 9) ----------------- #


def test_telemetry_endpoint_404(client: TestClient) -> None:
    assert client.get("/api/v1/analyses/nope/telemetry").status_code == 404


def test_telemetry_endpoint_null_when_incomplete(client: TestClient) -> None:
    analysis_id = _insert_analysis(status="running", analysis_id="a-tel-run")
    resp = client.get(f"/api/v1/analyses/{analysis_id}/telemetry")
    assert resp.status_code == 200
    assert resp.json() == {"svg": None}


def test_telemetry_endpoint_returns_svg(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from api.mongo import get_mongo
    from api.routes import analyses as analyses_route

    calls: list[tuple] = []

    def _fake_build(a, t, table):  # type: ignore[no-untyped-def]
        calls.append((a, t, table))
        return "<svg/>"

    monkeypatch.setattr(analyses_route, "build_analysis_telemetry_svg", _fake_build)
    analysis_id = _insert_analysis(status="complete", analysis_id="a-tel-ok")
    get_mongo().tests.insert_one({
        "_id": "TST-0001",
        "driver": "d",
        "test_rig_device_id": "DEV-0001",
        "environment_id": "ENV-0001",
        "experiment_id": "x",
        "pc_device_id": "DEV-0002",
        "config_id": "cfg-0001",
        "sessions": [],
    })
    resp = client.get(f"/api/v1/analyses/{analysis_id}/telemetry")
    assert resp.status_code == 200
    assert resp.json() == {"svg": "<svg/>"}
    assert len(calls) == 1, "build_analysis_telemetry_svg must have been called"
