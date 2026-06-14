"""Tests for the Analysis Pydantic models and CRUD routes."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
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
from tests.conftest import TestFactory


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


def test_analysis_create_requires_test_id():
    """test_id is required; session_id is optional (v2 — null = test-wide)."""
    with pytest.raises(ValidationError):
        AnalysisCreate(test_id="", session_id="s")  # min_length=1 on test_id
    ok = AnalysisCreate(test_id="t", session_id="s")
    assert ok.test_id == "t"
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
    t, s = _create_test_with_session(client, create_test)

    first = client.post(
        "/api/v1/analyses", json={"test_id": t, "session_id": s}
    ).json()["analysis_id"]
    second = client.post(
        "/api/v1/analyses", json={"test_id": t, "session_id": s}
    ).json()["analysis_id"]

    response = client.get(f"/api/v1/analyses?test_id={t}")
    items = response.json()["items"]
    assert items[0]["id"] == second
    assert items[1]["id"] == first


def test_list_analyses_pagination(client: TestClient, create_test: TestFactory) -> None:
    t, s = _create_test_with_session(client, create_test)

    for _ in range(5):
        client.post("/api/v1/analyses", json={"test_id": t, "session_id": s})

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
