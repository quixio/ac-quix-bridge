"""Tests for the test-manager MCP server mounted at /mcp.

Most tests are direct-callable against handler functions for unit-level coverage.
Two end-to-end tests exercise the X-API-Key auth middleware over HTTP.
"""

from datetime import datetime, timezone
from typing import Any, Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database
from testcontainers.mongodb import MongoDbContainer

from api.routes.mcp.handlers.core import (
    get_session as get_session_handler,
)
from api.routes.mcp.handlers.core import (
    get_test as get_test_handler,
)
from api.routes.mcp.handlers.core import (
    list_logbook as list_logbook_handler,
)
from api.routes.mcp.handlers.history import (
    list_recent_sessions_for_driver as list_recent_handler,
)
from api.routes.mcp.handlers.history import (
    list_sessions_for_test as list_sessions_handler,
)
from api.routes.mcp.handlers.lookups import (
    get_device as get_device_handler,
)
from api.routes.mcp.handlers.lookups import (
    get_driver as get_driver_handler,
)
from api.routes.mcp.handlers.lookups import (
    get_environment as get_environment_handler,
)
from api.routes.mcp.handlers.write import (
    save_analysis as save_analysis_handler,
)
from tests.conftest import DeviceFactory, DriverFactory, EnvironmentFactory, TestFactory


# --- Local Mongo handle fixture ------------------------------------------- #


@pytest.fixture
def mongo_db(
    mongo: None, mongo_container: MongoDbContainer
) -> Generator[Database[dict[str, Any]], None, None]:
    """Direct pymongo Database handle pointing at the testcontainers Mongo."""
    client = mongo_container.get_connection_client()
    yield client[mongo_container.dbname]
    client.close()


# --- Helpers -------------------------------------------------------------- #


def _create_test_with_session(
    client: TestClient,
    create_test: TestFactory,
    session_id: str = "2026-05-22T10:30:00",
    track: str = "ks_nurburgring",
    car_model: str = "bmw_1m",
) -> tuple[str, str]:
    """Create a test and attach one session. Returns (test_id, session_id)."""
    _, created = create_test()
    test_id = created["test_id"]
    response = client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={"session_id": session_id, "track": track, "car_model": car_model},
    )
    assert response.status_code == 200
    return test_id, session_id


def _make_pending_analysis(
    mongo_db: Database[dict[str, Any]],
    test_id: str,
    session_id: str,
    status: str = "running",
) -> str:
    aid = str(uuid4())
    mongo_db.analyses.insert_one(
        {
            "_id": aid,
            "schema_version": 1,
            "test_id": test_id,
            "session_id": session_id,
            "status": status,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "kpis": [],
            "requirements_check": [],
            "logbook_refs": [],
            "anomalies": [],
            "summary_md": "",
            "extra": {},
        }
    )
    return aid


# --- Auth (HTTP) ---------------------------------------------------------- #


@pytest.fixture
def configured_mcp_key(monkeypatch: pytest.MonkeyPatch) -> Generator[str, None, None]:
    """Set TESTMANAGER_MCP_API_KEY and bust the settings lru_cache so the
    middleware sees the configured value on its next request."""
    from api.settings import get_settings

    key = "test-mcp-key-abc123"
    monkeypatch.setenv("TESTMANAGER_MCP_API_KEY", key)
    get_settings.cache_clear()
    yield key
    get_settings.cache_clear()


def test_mcp_rejects_missing_api_key(
    client: TestClient, configured_mcp_key: str
) -> None:
    """Key configured + no X-API-Key header → 401."""
    response = client.post(
        "/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 401


def test_mcp_rejects_wrong_api_key(client: TestClient, configured_mcp_key: str) -> None:
    """Key configured + wrong X-API-Key → 401."""
    response = client.post(
        "/mcp/",
        headers={
            "X-API-Key": "wrong-key",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 401


def test_mcp_accepts_correct_api_key(
    client: TestClient, configured_mcp_key: str
) -> None:
    """Correct X-API-Key passes the middleware (response is not 401)."""
    response = client.post(
        "/mcp/",
        headers={
            "X-API-Key": configured_mcp_key,
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
    )
    assert response.status_code != 401


# --- Core: get_test, get_session, list_logbook ---------------------------- #


def test_get_test_returns_resolved_names(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    test_id, _ = _create_test_with_session(client, create_test)
    result = get_test_handler(mongo_db, test_id=test_id)
    assert result["test_id"] == test_id
    assert result["pc_device_name"] is not None
    assert result["test_rig_device_name"] is not None
    assert result["environment_name"] is not None
    assert len(result["sessions"]) == 1


def test_get_test_unknown_raises_value_error(
    mongo_db: Database[dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="not found"):
        get_test_handler(mongo_db, test_id="TST-9999")


def test_get_session_returns_session_info(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    test_id, sid = _create_test_with_session(client, create_test)
    result = get_session_handler(mongo_db, test_id=test_id, session_id=sid)
    assert result["session_id"] == sid
    assert result["track"] == "ks_nurburgring"
    assert result["car_model"] == "bmw_1m"


def test_get_session_unknown_session_raises(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    test_id, _ = _create_test_with_session(client, create_test)
    with pytest.raises(ValueError, match="not on test"):
        get_session_handler(mongo_db, test_id=test_id, session_id="2099-01-01T00:00:00")


def test_list_logbook_filter_by_session(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    test_id, sid = _create_test_with_session(client, create_test)

    client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "tied", "session_id": sid},
    )
    client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "wide"},
    )

    items = list_logbook_handler(mongo_db, test_id=test_id, session_id=sid)
    assert [i["content"] for i in items] == ["tied"]

    items = list_logbook_handler(
        mongo_db, test_id=test_id, session_id=sid, include_test_wide=True
    )
    assert {i["content"] for i in items} == {"tied", "wide"}


# --- Lookups: get_driver, get_device, get_environment --------------------- #


def test_get_driver_by_id(
    mongo_db: Database[dict[str, Any]], create_driver: DriverFactory
) -> None:
    _, drv = create_driver(name="Alice")
    result = get_driver_handler(mongo_db, id=drv["driver_id"])
    assert result["driver_id"] == drv["driver_id"]
    assert result["name"] == "Alice"


def test_get_driver_unknown_raises(mongo_db: Database[dict[str, Any]]) -> None:
    with pytest.raises(ValueError, match="not found"):
        get_driver_handler(mongo_db, id="DRV-9999")


def test_get_device_by_id(
    mongo_db: Database[dict[str, Any]], create_device: DeviceFactory
) -> None:
    _, dev = create_device(name="Sim PC", category="pc")
    result = get_device_handler(mongo_db, id=dev["device_id"])
    assert result["device_id"] == dev["device_id"]
    assert result["name"] == "Sim PC"


def test_get_environment_by_id(
    mongo_db: Database[dict[str, Any]], create_environment: EnvironmentFactory
) -> None:
    _, env = create_environment(name="Lab A")
    result = get_environment_handler(mongo_db, id=env["environment_id"])
    assert result["environment_id"] == env["environment_id"]
    assert result["name"] == "Lab A"


# --- History: list_sessions_for_test, list_recent_sessions_for_driver ---- #


def test_list_sessions_for_test_sorted_desc(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    _, created = create_test()
    test_id = created["test_id"]
    early = "2026-05-22T10:30:00"
    later = "2026-05-22T12:00:00"
    for sid in (early, later):
        client.post(
            f"/api/v1/tests/{test_id}/sessions",
            json={"session_id": sid, "track": "ks_nurburgring", "car_model": "bmw_1m"},
        )

    sessions = list_sessions_handler(mongo_db, test_id=test_id)
    assert [s["session_id"] for s in sessions] == [later, early]


def test_list_sessions_for_test_unknown_raises(
    mongo_db: Database[dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="not found"):
        list_sessions_handler(mongo_db, test_id="TST-9999")


def test_list_recent_sessions_for_driver_limits_and_orders(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
    create_driver: DriverFactory,
) -> None:
    driver_name = "Carlos Sainz"
    _, driver = create_driver(name=driver_name)
    driver_id = driver["driver_id"]
    session_ids = [
        "2026-05-20T10:00:00",
        "2026-05-21T10:00:00",
        "2026-05-22T10:00:00",
    ]
    # Two tests sharing the driver, three sessions total
    _, t1 = create_test(driver=driver_name)
    _, t2 = create_test(driver=driver_name)
    client.post(
        f"/api/v1/tests/{t1['test_id']}/sessions",
        json={
            "session_id": session_ids[0],
            "track": "ks_nurburgring",
            "car_model": "bmw_1m",
        },
    )
    client.post(
        f"/api/v1/tests/{t2['test_id']}/sessions",
        json={
            "session_id": session_ids[1],
            "track": "ks_nurburgring",
            "car_model": "bmw_1m",
        },
    )
    client.post(
        f"/api/v1/tests/{t2['test_id']}/sessions",
        json={
            "session_id": session_ids[2],
            "track": "ks_nurburgring",
            "car_model": "bmw_1m",
        },
    )

    result = list_recent_handler(mongo_db, driver_id=driver_id, limit=2)
    assert len(result) == 2
    assert result[0]["session_id"] == session_ids[2]
    assert result[1]["session_id"] == session_ids[1]


def test_list_recent_sessions_for_driver_unknown_id_raises(
    mongo_db: Database[dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="not found"):
        list_recent_handler(mongo_db, driver_id="DRV-9999")


# --- Write: save_analysis ------------------------------------------------- #


def test_save_analysis_writes_payload(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    test_id, sid = _create_test_with_session(client, create_test)
    aid = _make_pending_analysis(mongo_db, test_id, sid)

    result = save_analysis_handler(
        mongo_db,
        analysis_id=aid,
        summary_md="## Pace\n\nDriver did fine.",
        kpis=[{"name": "best_lap", "value": "1:45.321"}],
        requirements_check=[],
        logbook_refs=[],
        anomalies=[],
        extra={"weather": "20C dry"},
    )
    assert result == {"ok": True, "analysis_id": aid}

    doc = mongo_db.analyses.find_one({"_id": aid})
    assert doc is not None
    assert doc["status"] == "complete"
    assert doc["summary_md"].startswith("## Pace")
    assert doc["kpis"] == [
        {"name": "best_lap", "value": "1:45.321", "unit": None, "notes": None, "session_id": None}
    ]
    assert doc["extra"] == {"weather": "20C dry"}


def test_save_analysis_unknown_id_raises(
    mongo_db: Database[dict[str, Any]],
) -> None:
    with pytest.raises(ValueError, match="not found"):
        save_analysis_handler(mongo_db, analysis_id="nonexistent-uuid", summary_md="x")


def test_save_analysis_double_call_rejected(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    test_id, sid = _create_test_with_session(client, create_test)
    aid = _make_pending_analysis(mongo_db, test_id, sid)

    save_analysis_handler(mongo_db, analysis_id=aid, summary_md="first")
    with pytest.raises(ValueError, match="already complete"):
        save_analysis_handler(mongo_db, analysis_id=aid, summary_md="second")


def test_save_analysis_invalid_payload_raises(
    mongo_db: Database[dict[str, Any]],
    client: TestClient,
    create_test: TestFactory,
) -> None:
    test_id, sid = _create_test_with_session(client, create_test)
    aid = _make_pending_analysis(mongo_db, test_id, sid)

    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        save_analysis_handler(mongo_db, analysis_id=aid, summary_md="")
