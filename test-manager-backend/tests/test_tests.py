import re
from typing import cast

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import Mock

from api.config_api import get_config_api_client
from api.models import Test as TestModel
from api.mongo import get_mongo
from api.routes.tests import build_partition_values
from tests.conftest import TestFactory, DeviceFactory, EnvironmentFactory

UTC_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ============================================================================
# Create
# ============================================================================


def test_create_test(create_test: TestFactory, config_api: httpx.Client) -> None:
    """Test that a test can be created with auto-generated ID and config synced to DCM."""
    input_data, output_data = create_test()

    assert output_data["test_id"].startswith("TST-")
    assert output_data["experiment_id"] == input_data["experiment_id"]
    assert output_data["pc_device_id"] == input_data["pc_device_id"]
    assert output_data["test_rig_device_id"] == input_data["test_rig_device_id"]
    assert output_data["environment_id"] == input_data["environment_id"]
    assert output_data["driver"] == input_data["driver"]
    assert output_data["requirements"] == input_data["requirements"]
    assert output_data["sessions"] == []
    assert output_data["config_id"] is not None
    assert output_data["config_version"] is not None
    assert UTC_ISO_DATETIME.match(output_data["created_at"])
    assert UTC_ISO_DATETIME.match(output_data["updated_at"])

    # Verify config was created in DCM
    config_id = output_data["config_id"]
    config_content = config_api.get(
        f"/api/v1/configurations/{config_id}/content"
    ).json()
    assert config_content["test_id"] == output_data["test_id"]
    assert config_content["experiment_id"] == input_data["experiment_id"]
    assert config_content["driver"] == input_data["driver"].lower()


def test_create_test_auto_increment_ids(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test that test IDs auto-increment."""
    _, t1 = create_test()
    _, t2 = create_test()
    assert t1["test_id"] == "TST-0001"
    assert t2["test_id"] == "TST-0002"


def test_create_test_device_not_found(client: TestClient) -> None:
    """Test that creating a test with non-existent device returns 404."""
    response = client.post(
        "/api/v1/tests",
        json={
            "experiment_id": "exp1",
            "pc_device_id": "nonexistent",
            "test_rig_device_id": "also-nonexistent",
            "environment_id": "env1",
            "driver": "Tomas",
        },
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_create_test_resolves_names(
    create_test: TestFactory,
    client: TestClient,
) -> None:
    """Test that the response includes resolved device and environment names."""
    _, output_data = create_test()
    test_id = output_data["test_id"]

    response = client.get(f"/api/v1/tests/{test_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["pc_device_name"] is not None
    assert data["test_rig_device_name"] is not None
    assert data["environment_name"] is not None


# ============================================================================
# List
# ============================================================================


def test_list_tests_empty(client: TestClient) -> None:
    """Test that an empty paginated response is returned when no tests exist."""
    response = client.get("/api/v1/tests")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total_pages"] == 0


def test_list_tests_with_data(create_test: TestFactory, client: TestClient) -> None:
    """Test listing tests with filtering."""
    create_test(driver="Alice")
    create_test(driver="Bob")

    response = client.get("/api/v1/tests")
    assert response.status_code == 200
    assert response.json()["total"] == 2

    # Filter by driver
    response = client.get("/api/v1/tests?driver=Alice")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["driver"] == "Alice"


def test_list_tests_text_search(create_test: TestFactory, client: TestClient) -> None:
    """Test text search with q parameter."""
    create_test(experiment_id="tyre-pressure-study", driver="Daniel")
    create_test(experiment_id="brake-temp-analysis", driver="Tomas")

    response = client.get("/api/v1/tests?q=tyre")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["experiment_id"] == "tyre-pressure-study"


# ============================================================================
# Get
# ============================================================================


def test_get_test_not_found(client: TestClient) -> None:
    """Test that a 404 is returned for a non-existent test."""
    response = client.get("/api/v1/tests/nonexistent")
    assert response.status_code == 404


def test_get_test_found(create_test: TestFactory, client: TestClient) -> None:
    """Test retrieving a single test by ID."""
    _, created = create_test(experiment_id="my-experiment")

    response = client.get(f"/api/v1/tests/{created['test_id']}")
    assert response.status_code == 200
    assert response.json()["experiment_id"] == "my-experiment"


def test_get_test_full(create_test: TestFactory, client: TestClient) -> None:
    """Test the full endpoint returns test with logbook."""
    _, created = create_test()
    test_id = created["test_id"]

    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "Test note"})

    response = client.get(f"/api/v1/tests/{test_id}/full")
    assert response.status_code == 200
    data = response.json()

    assert "test" in data
    assert "logbook" in data
    assert data["test"]["test_id"] == test_id
    assert len(data["logbook"]) == 1


# ============================================================================
# Update
# ============================================================================


def test_update_test(
    create_test: TestFactory,
    client: TestClient,
    config_api: httpx.Client,
) -> None:
    """Test updating a test and verifying config is re-synced to DCM."""
    _, created = create_test(experiment_id="original-exp", driver="Alice")
    test_id = created["test_id"]
    original_created_at = created["created_at"]

    response = client.put(
        f"/api/v1/tests/{test_id}",
        json={
            "experiment_id": "updated-exp",
            "driver": "Bob",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["experiment_id"] == "updated-exp"
    assert data["driver"] == "Bob"
    assert data["created_at"] == original_created_at

    # Verify persisted
    response = client.get(f"/api/v1/tests/{test_id}")
    assert response.json()["experiment_id"] == "updated-exp"

    # Verify config updated in DCM
    config_id = data["config_id"]
    config_content = config_api.get(
        f"/api/v1/configurations/{config_id}/content"
    ).json()
    assert config_content["experiment_id"] == "updated-exp"
    assert config_content["driver"] == "bob"  # lowercased


def test_update_test_not_found(client: TestClient) -> None:
    """Test that updating a non-existent test returns 404."""
    response = client.put("/api/v1/tests/nonexistent", json={"driver": "X"})
    assert response.status_code == 404


def test_update_test_no_data(create_test: TestFactory, client: TestClient) -> None:
    """Test that updating with no fields returns 400."""
    _, created = create_test()
    response = client.put(f"/api/v1/tests/{created['test_id']}", json={})
    assert response.status_code == 400


# ============================================================================
# Delete
# ============================================================================


def test_delete_test(
    create_test: TestFactory,
    client: TestClient,
    config_api: httpx.Client,
) -> None:
    """Test deleting a test removes test, logbook, and config version."""
    _, created = create_test()
    test_id = created["test_id"]
    config_id = created["config_id"]
    config_version = created["config_version"]

    # Add logbook entries
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "Entry 1"})
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "Entry 2"})

    # Delete
    response = client.delete(f"/api/v1/tests/{test_id}")
    assert response.status_code == 204

    # Test gone
    assert client.get(f"/api/v1/tests/{test_id}").status_code == 404

    # Logbook entries gone
    response = client.get(f"/api/v1/tests/{test_id}/logbook")
    assert response.json() == []

    # Config version gone
    response = config_api.get(
        f"/api/v1/configurations/{config_id}/versions/{config_version}/content"
    )
    assert response.status_code == 404


def test_delete_test_not_found(client: TestClient) -> None:
    """Test that deleting a non-existent test returns 404."""
    response = client.delete("/api/v1/tests/nonexistent")
    assert response.status_code == 404


# ============================================================================
# Requirements field — AI prompt pipeline will read this, so pin the contract.
# ============================================================================


MULTILINE_REQS = (
    "The driver shall finish Monza under 55.250s.\n"
    "The car shall not exceed 3.5G longitudinal.\n"
    "Tyre temperature shall stay below 80°C."
)


def test_create_test_with_requirements(
    create_test: TestFactory, config_api: httpx.Client
) -> None:
    """Multi-line requirements round-trip through create + DCM config content."""
    _, created = create_test(requirements=MULTILINE_REQS)

    assert created["requirements"] == MULTILINE_REQS

    content = config_api.get(
        f"/api/v1/configurations/{created['config_id']}/versions/{created['config_version']}/content"
    ).json()
    assert content["requirements"] == MULTILINE_REQS


def test_update_test_requirements(
    create_test: TestFactory, client: TestClient, config_api: httpx.Client
) -> None:
    """PUT replaces requirements and a new DCM version carries the new value."""
    _, created = create_test(requirements="old requirements")
    test_id = created["test_id"]

    response = client.put(
        f"/api/v1/tests/{test_id}", json={"requirements": MULTILINE_REQS}
    )
    assert response.status_code == 200
    updated = response.json()
    assert updated["requirements"] == MULTILINE_REQS
    assert updated["config_version"] == created["config_version"] + 1

    content = config_api.get(
        f"/api/v1/configurations/{updated['config_id']}/versions/{updated['config_version']}/content"
    ).json()
    assert content["requirements"] == MULTILINE_REQS


def test_activate_preserves_requirements_in_dcm(
    create_test: TestFactory, client: TestClient, config_api: httpx.Client
) -> None:
    """Activate pushes a fresh DCM version that still carries requirements."""
    _, created = create_test(requirements=MULTILINE_REQS)
    test_id = created["test_id"]

    response = client.post(f"/api/v1/tests/{test_id}/activate")
    assert response.status_code == 200
    activated = response.json()
    assert activated["config_version"] == created["config_version"] + 1

    content = config_api.get(
        f"/api/v1/configurations/{activated['config_id']}/versions/{activated['config_version']}/content"
    ).json()
    assert content["requirements"] == MULTILINE_REQS


def test_create_test_with_mode(
    create_test: TestFactory, config_api: httpx.Client
) -> None:
    """mode round-trips through create + DCM config content (not a lake partition)."""
    _, created = create_test(mode="pro")

    assert created["mode"] == "pro"

    content = config_api.get(
        f"/api/v1/configurations/{created['config_id']}/versions/{created['config_version']}/content"
    ).json()
    assert content["mode"] == "pro"


def test_create_test_without_mode_defaults_none(create_test: TestFactory) -> None:
    """mode is optional on the backend; omitting it stores null."""
    _, created = create_test()
    assert created["mode"] is None


def test_update_test_mode(
    create_test: TestFactory, client: TestClient, config_api: httpx.Client
) -> None:
    """PUT changes mode and a new DCM version carries the new value."""
    _, created = create_test(mode="easy")
    test_id = created["test_id"]

    response = client.put(f"/api/v1/tests/{test_id}", json={"mode": "pro"})
    assert response.status_code == 200
    updated = response.json()
    assert updated["mode"] == "pro"

    content = config_api.get(
        f"/api/v1/configurations/{updated['config_id']}/versions/{updated['config_version']}/content"
    ).json()
    assert content["mode"] == "pro"


def test_create_test_invalid_mode_422(client: TestClient) -> None:
    """An unknown mode value is rejected by validation before any DCM call."""
    response = client.post(
        "/api/v1/tests",
        json={
            "experiment_id": "exp",
            "pc_device_id": "DEV-0001",
            "test_rig_device_id": "DEV-0002",
            "environment_id": "ENV-0001",
            "driver": "Test Driver",
            "mode": "insane",
        },
    )
    assert response.status_code == 422


def test_update_test_clears_mode(
    create_test: TestFactory, client: TestClient, config_api: httpx.Client
) -> None:
    """PUT {mode: null} clears a previously-set mode in Mongo and DCM content."""
    _, created = create_test(mode="pro")
    test_id = created["test_id"]

    response = client.put(f"/api/v1/tests/{test_id}", json={"mode": None})
    assert response.status_code == 200
    updated = response.json()
    assert updated["mode"] is None

    content = config_api.get(
        f"/api/v1/configurations/{updated['config_id']}/versions/{updated['config_version']}/content"
    ).json()
    assert content["mode"] is None


def test_activate_preserves_mode_in_dcm(
    create_test: TestFactory, client: TestClient, config_api: httpx.Client
) -> None:
    """Activate pushes a fresh DCM version that still carries mode."""
    _, created = create_test(mode="pro")
    test_id = created["test_id"]

    response = client.post(f"/api/v1/tests/{test_id}/activate")
    assert response.status_code == 200
    activated = response.json()

    content = config_api.get(
        f"/api/v1/configurations/{activated['config_id']}/versions/{activated['config_version']}/content"
    ).json()
    assert content["mode"] == "pro"


def test_delete_cleans_all_orphan_versions_of_that_test(
    create_test: TestFactory,
    create_device: DeviceFactory,
    create_environment: EnvironmentFactory,
    client: TestClient,
    config_api: httpx.Client,
) -> None:
    """Delete must clean EVERY DCM version belonging to the deleted test.

    Regression for the orphan-becomes-latest scenario observed in local dev:
    Activate-then-Edit leaves an earlier version orphaned. Deleting the test
    only removed the *current* pointer, so an orphan could bubble up to be the
    max version — the AC bridge then enriches new telemetry with the deleted
    test's content. Fix: on delete, remove every version whose content.test_id
    matches the one being deleted.
    """
    _, pc = create_device(name="SharedPC", category="pc")
    _, rig = create_device(name="SharedRig", category="test_rig")
    _, env = create_environment(name="SharedEnv")

    # B is the sibling whose version must end up as the latest.
    _, b = create_test(
        pc_device_id=pc["device_id"],
        test_rig_device_id=rig["device_id"],
        environment_id=env["environment_id"],
        experiment_id="b",
    )
    _, a = create_test(
        pc_device_id=pc["device_id"],
        test_rig_device_id=rig["device_id"],
        environment_id=env["environment_id"],
        experiment_id="a",
    )
    config_id = a["config_id"]
    assert b["config_id"] == config_id

    # Activate A (creates another A-version) then Edit (creates yet another).
    client.post(f"/api/v1/tests/{a['test_id']}/activate").raise_for_status()
    client.put(
        f"/api/v1/tests/{a['test_id']}", json={"experiment_id": "a-edited"}
    ).raise_for_status()

    # Now: the config should have >= 4 versions, with ≥3 belonging to A
    # (create, activate, edit) and 1 to B.
    versions_before = config_api.get(
        f"/api/v1/configurations/{config_id}/versions"
    ).json()["data"]
    a_versions_before = [
        v["metadata"]["version"]
        for v in versions_before
        if config_api.get(
            f"/api/v1/configurations/{config_id}/versions/{v['metadata']['version']}/content"
        ).json()["test_id"]
        == a["test_id"]
    ]
    assert len(a_versions_before) >= 3

    # Delete A.
    assert client.delete(f"/api/v1/tests/{a['test_id']}").status_code == 204

    # Zero versions carrying A's test_id may remain.
    versions_after = config_api.get(
        f"/api/v1/configurations/{config_id}/versions"
    ).json()["data"]
    for v in versions_after:
        vnum = v["metadata"]["version"]
        content = config_api.get(
            f"/api/v1/configurations/{config_id}/versions/{vnum}/content"
        ).json()
        assert content["test_id"] != a["test_id"], (
            f"orphan v{vnum} for deleted test {a['test_id']} survived"
        )

    # B's telemetry-params must still resolve (max version = B's).
    params = client.get(f"/api/v1/tests/{b['test_id']}/telemetry-params")
    assert params.status_code == 200
    assert params.json()["experiment"] == "b"


# ============================================================================
# Activate
# ============================================================================


def test_activate_test_creates_new_dcm_version(
    create_test: TestFactory,
    client: TestClient,
    config_api: httpx.Client,
) -> None:
    """Activating bumps the test's config_version to a new DCM version.

    The previous version stays in DCM (orphaned); new version carries the
    current test content, making it the latest for bridge enrichment.
    """
    _, created = create_test(experiment_id="my-exp", driver="Daniel")
    test_id = created["test_id"]
    old_config_id = created["config_id"]
    old_version = created["config_version"]

    response = client.post(f"/api/v1/tests/{test_id}/activate")
    assert response.status_code == 200
    data = response.json()
    assert data["config_id"] == old_config_id
    assert data["config_version"] == old_version + 1

    # Old version still exists (orphan).
    assert (
        config_api.get(
            f"/api/v1/configurations/{old_config_id}/versions/{old_version}/content"
        ).status_code
        == 200
    )
    # New version has the current test's content.
    new_content = config_api.get(
        f"/api/v1/configurations/{old_config_id}/versions/{data['config_version']}/content"
    ).json()
    assert new_content["test_id"] == test_id
    assert new_content["experiment_id"] == "my-exp"
    assert new_content["driver"] == "daniel"


def test_activate_test_not_found(client: TestClient) -> None:
    """Activating a non-existent test returns 404."""
    response = client.post("/api/v1/tests/TST-9999/activate")
    assert response.status_code == 404


def test_activate_preserves_sibling_versions(
    create_test: TestFactory,
    create_device: DeviceFactory,
    create_environment: EnvironmentFactory,
    client: TestClient,
    config_api: httpx.Client,
) -> None:
    """Activating one test leaves sibling tests on the same hostname untouched."""
    _, pc = create_device(name="SharedPC", category="pc")
    _, rig = create_device(name="SharedRig", category="test_rig")
    _, env = create_environment(name="SharedEnv")

    _, a = create_test(
        pc_device_id=pc["device_id"],
        test_rig_device_id=rig["device_id"],
        environment_id=env["environment_id"],
        experiment_id="a",
    )
    _, b = create_test(
        pc_device_id=pc["device_id"],
        test_rig_device_id=rig["device_id"],
        environment_id=env["environment_id"],
        experiment_id="b",
    )
    assert a["config_id"] == b["config_id"]

    # Activate A; B must not change.
    client.post(f"/api/v1/tests/{a['test_id']}/activate").raise_for_status()

    b_refreshed = client.get(f"/api/v1/tests/{b['test_id']}").json()
    assert b_refreshed["config_version"] == b["config_version"]

    # B's telemetry-params still resolve.
    assert (
        client.get(f"/api/v1/tests/{b['test_id']}/telemetry-params").status_code == 200
    )


def test_delete_one_test_preserves_siblings_on_same_hostname(
    create_test: TestFactory,
    create_device: DeviceFactory,
    create_environment: EnvironmentFactory,
    client: TestClient,
    config_api: httpx.Client,
) -> None:
    """Regression test for the 2026-04-15 bug where deleting one test nuked the
    entire shared DCM config, wiping sibling tests' history.

    Multiple tests targeting the same PC (same target_key) share one DCM
    config_id; each test owns a distinct version. Deleting one test must
    remove only its own version — sibling tests' versions and the config
    itself must stay intact and resolvable via /telemetry-params.
    """
    _, pc = create_device(name="SharedPC", category="pc")
    _, rig = create_device(name="SharedRig", category="test_rig")
    _, env = create_environment(name="SharedEnv")

    siblings = []
    for i in range(3):
        _, t = create_test(
            pc_device_id=pc["device_id"],
            test_rig_device_id=rig["device_id"],
            environment_id=env["environment_id"],
            experiment_id=f"exp-{i}",
        )
        siblings.append(t)

    # All siblings should share one config_id but hold distinct versions.
    shared_config_id = siblings[0]["config_id"]
    assert all(s["config_id"] == shared_config_id for s in siblings)
    versions = [s["config_version"] for s in siblings]
    assert len(set(versions)) == 3

    victim, survivor_a, survivor_b = siblings

    # Delete the middle test.
    assert client.delete(f"/api/v1/tests/{victim['test_id']}").status_code == 204

    # Victim's specific version is gone.
    resp = config_api.get(
        f"/api/v1/configurations/{shared_config_id}/versions/{victim['config_version']}/content"
    )
    assert resp.status_code == 404

    # Survivors' specific versions still resolve.
    for s in (survivor_a, survivor_b):
        resp = config_api.get(
            f"/api/v1/configurations/{shared_config_id}/versions/{s['config_version']}/content"
        )
        assert resp.status_code == 200
        assert resp.json()["test_id"] == s["test_id"]

    # /telemetry-params still works end-to-end for survivors.
    for s in (survivor_a, survivor_b):
        resp = client.get(f"/api/v1/tests/{s['test_id']}/telemetry-params")
        assert resp.status_code == 200
        assert resp.json()["experiment"] == s["experiment_id"]

    # The shared config itself must still exist.
    assert (
        config_api.get(f"/api/v1/configurations/{shared_config_id}").status_code == 200
    )


# ============================================================================
# Config API error handling
# ============================================================================


def test_create_test_config_api_error(
    client: TestClient,
    create_device: DeviceFactory,
    create_environment: EnvironmentFactory,
) -> None:
    """Test that create returns 424 when config API fails."""
    _, pc = create_device(name="PC", category="pc")
    _, rig = create_device(name="Rig", category="test_rig")
    _, env = create_environment(name="Env")

    # Mock config API to fail
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server error", request=Mock(), response=mock_response
    )
    mock_client = Mock()
    mock_client.post.return_value = mock_response

    client.app.dependency_overrides[get_config_api_client] = lambda: mock_client  # ty: ignore[unresolved-attribute]

    try:
        response = client.post(
            "/api/v1/tests",
            json={
                "experiment_id": "exp1",
                "pc_device_id": pc["device_id"],
                "test_rig_device_id": rig["device_id"],
                "environment_id": env["environment_id"],
                "driver": "Tomas",
            },
        )
        assert response.status_code == 424
        assert "Failed to create configuration" in response.json()["detail"]
    finally:
        client.app.dependency_overrides.clear()  # ty: ignore[unresolved-attribute]


# ============================================================================
# Sessions
# ============================================================================


def test_add_session(create_test: TestFactory, client: TestClient) -> None:
    """Test adding a session to a test."""
    _, created = create_test()
    test_id = created["test_id"]

    session = {
        "session_id": "2026-04-16T10:30:00",
        "track": "ks_nurburgring",
        "car_model": "bmw_1m",
    }
    response = client.post(f"/api/v1/tests/{test_id}/sessions", json=session)
    assert response.status_code == 200
    data = response.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["session_id"] == "2026-04-16T10:30:00"
    assert data["sessions"][0]["track"] == "ks_nurburgring"


def test_add_session_deduplicate(create_test: TestFactory, client: TestClient) -> None:
    """Test that duplicate session_id is skipped."""
    _, created = create_test()
    test_id = created["test_id"]

    session = {
        "session_id": "2026-04-16T10:30:00",
        "track": "ks_nurburgring",
        "car_model": "bmw_1m",
    }
    client.post(f"/api/v1/tests/{test_id}/sessions", json=session)
    client.post(f"/api/v1/tests/{test_id}/sessions", json=session)

    response = client.get(f"/api/v1/tests/{test_id}")
    assert len(response.json()["sessions"]) == 1


def test_add_multiple_sessions(create_test: TestFactory, client: TestClient) -> None:
    """Distinct sessions append in order; telemetry-params keys off sessions[0]."""
    _, created = create_test()
    test_id = created["test_id"]

    sessions = [
        {
            "session_id": "2026-04-16T09:00:00Z",
            "track": "monza",
            "car_model": "ferrari_488",
        },
        {"session_id": "2026-04-16T10:00:00Z", "track": "spa", "car_model": "bmw_1m"},
        {
            "session_id": "2026-04-16T11:00:00Z",
            "track": "nurburgring",
            "car_model": "mclaren_720s",
        },
    ]
    for s in sessions:
        r = client.post(f"/api/v1/tests/{test_id}/sessions", json=s)
        assert r.status_code == 200

    data = client.get(f"/api/v1/tests/{test_id}").json()
    assert [s["session_id"] for s in data["sessions"]] == [
        s["session_id"] for s in sessions
    ]

    params = client.get(f"/api/v1/tests/{test_id}/telemetry-params").json()
    assert params["track"] == "monza"
    assert params["carModel"] == "ferrari_488"
    assert params["session_ids"] == [s["session_id"] for s in sessions]


def test_add_session_test_not_found(client: TestClient) -> None:
    """Adding a session to a non-existent test returns 404."""
    response = client.post(
        "/api/v1/tests/TST-9999/sessions",
        json={"session_id": "s-1", "track": "monza", "car_model": "ferrari_488"},
    )
    assert response.status_code == 404


def test_edit_test_preserves_sessions(
    create_test: TestFactory, client: TestClient
) -> None:
    """Updating a test must not drop its sessions array."""
    _, created = create_test()
    test_id = created["test_id"]

    client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={"session_id": "s-1", "track": "monza", "car_model": "ferrari_488"},
    )
    client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={"session_id": "s-2", "track": "spa", "car_model": "bmw_1m"},
    )

    # Edit the test — this re-pushes config to DCM and overwrites test doc fields.
    r = client.put(f"/api/v1/tests/{test_id}", json={"driver": "Someone"})
    assert r.status_code == 200

    sessions = client.get(f"/api/v1/tests/{test_id}").json()["sessions"]
    assert [s["session_id"] for s in sessions] == ["s-1", "s-2"]


# ============================================================================
# Telemetry params
# ============================================================================


def test_get_telemetry_params(
    create_test: TestFactory,
    client: TestClient,
) -> None:
    """Test fetching telemetry params for a test."""
    _, created = create_test(experiment_id="tyre-test", driver="Daniel")
    test_id = created["test_id"]

    # Add a session so track/carModel come from it
    client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={
            "session_id": "session-1",
            "track": "monza",
            "car_model": "ferrari_488",
        },
    )

    response = client.get(f"/api/v1/tests/{test_id}/telemetry-params")
    assert response.status_code == 200
    data = response.json()
    assert data["experiment"] == "tyre-test"
    assert data["driver"] == "daniel"  # lowercased
    assert data["track"] == "monza"
    assert data["carModel"] == "ferrari_488"
    assert data["session_ids"] == ["session-1"]
    # Lakehouse embed reads the per-env table from here; defaults to ac_telemetry.
    assert data["table_name"] == "ac_telemetry"


def test_get_telemetry_params_no_sessions(
    create_test: TestFactory,
    client: TestClient,
) -> None:
    """A test with no sessions returns null track/carModel (not a fabricated
    default) so consumers omit those partition filters."""
    _, created = create_test(experiment_id="tyre-test", driver="Daniel")

    response = client.get(f"/api/v1/tests/{created['test_id']}/telemetry-params")
    assert response.status_code == 200
    data = response.json()
    assert data["experiment"] == "tyre-test"
    assert data["driver"] == "daniel"
    assert data["track"] is None
    assert data["carModel"] is None
    assert data["session_ids"] == []
    assert data["table_name"] == "ac_telemetry"


# ============================================================================
# Filter endpoints
# ============================================================================


def test_filter_experiment_ids(create_test: TestFactory, client: TestClient) -> None:
    """Test getting distinct experiment IDs."""
    create_test(experiment_id="exp-alpha")
    create_test(experiment_id="exp-beta")
    create_test(experiment_id="exp-alpha")  # duplicate

    response = client.get("/api/v1/tests/filters/experiment-ids")
    assert response.status_code == 200
    assert sorted(response.json()) == ["exp-alpha", "exp-beta"]


def test_filter_drivers(create_test: TestFactory, client: TestClient) -> None:
    """Test getting distinct drivers."""
    create_test(driver="Alice")
    create_test(driver="Bob")

    response = client.get("/api/v1/tests/filters/drivers")
    assert response.status_code == 200
    assert sorted(response.json()) == ["Alice", "Bob"]


def test_last_used_empty_when_none(client: TestClient) -> None:
    """No tests at all → all-null fields, empty requirements."""
    response = client.get("/api/v1/tests/filters/last-used")
    assert response.status_code == 200
    data = response.json()
    assert data["requirements"] == ""
    assert data["pc_device_id"] is None
    assert data["test_rig_device_id"] is None
    assert data["environment_id"] is None
    assert data["driver"] is None
    assert data["experiment_id"] is None
    assert data["mode"] is None


def test_last_used_returns_latest_test_fields(
    create_test: TestFactory, client: TestClient
) -> None:
    """Returns every prefillable field from the most recently created test."""
    create_test(experiment_id="old_exp", driver="Old Driver", requirements="old")
    _, second = create_test(
        experiment_id="new_exp",
        driver="New Driver",
        mode="pro",
        requirements="new reqs",
    )

    response = client.get("/api/v1/tests/filters/last-used")
    assert response.status_code == 200
    data = response.json()
    assert data["experiment_id"] == "new_exp"
    assert data["driver"] == "New Driver"
    assert data["mode"] == "pro"
    assert data["requirements"] == "new reqs"
    assert data["pc_device_id"] == second["pc_device_id"]
    assert data["test_rig_device_id"] == second["test_rig_device_id"]
    assert data["environment_id"] == second["environment_id"]


def test_last_used_takes_requirements_as_is(
    create_test: TestFactory, client: TestClient
) -> None:
    """Unlike the old endpoint, it does NOT skip a blank-requirements latest test."""
    create_test(requirements="had reqs")
    create_test(requirements="")  # newest, blank — taken verbatim

    response = client.get("/api/v1/tests/filters/last-used")
    assert response.status_code == 200
    assert response.json()["requirements"] == ""


# ============================================================================
# Pagination
# ============================================================================


def test_pagination(create_test: TestFactory, client: TestClient) -> None:
    """Test basic pagination."""
    for i in range(5):
        create_test(experiment_id=f"exp-{i}")

    response = client.get("/api/v1/tests?page=1&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert data["page"] == 1
    assert data["total_pages"] == 1
    assert len(data["items"]) == 5


def test_pagination_multiple_pages(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test pagination across multiple pages."""
    for i in range(25):
        create_test(experiment_id=f"exp-{i:03d}")

    response = client.get("/api/v1/tests?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 25
    assert data["total_pages"] == 3
    assert len(data["items"]) == 10

    response = client.get("/api/v1/tests?page=3&page_size=10")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 5


# ============================================================================
# build_partition_values — unit tests for fallback behaviour
# ============================================================================


def test_build_partition_values_falls_back_when_refs_missing(
    client: TestClient,
) -> None:
    """When Device/Environment refs can't be resolved, use raw IDs.

    Normal deletes are blocked by referential integrity (409), but direct
    Mongo writes or migrations can leave orphan references. The fallback
    keeps /telemetry-params functional instead of crashing.
    """
    mongo = get_mongo()
    test = TestModel(
        _id="TST-ORPHAN",
        config_id="c1",
        experiment_id="exp-a",
        pc_device_id="DEV-GONE-PC",
        test_rig_device_id="DEV-GONE-RIG",
        environment_id="ENV-GONE",
        driver="Alice",
        requirements="",
    )
    result = build_partition_values(mongo, test)
    assert result == {
        "environment": "ENV-GONE",
        "test_rig": "DEV-GONE-RIG",
        "experiment": "exp-a",
        "driver": "alice",
    }


# ============================================================================
# DCM unreachable — guard against httpx.ConnectError / network failures
# ============================================================================


def _break_dcm(client: TestClient) -> None:
    """Swap the config_api dependency with one that raises httpx.ConnectError on every call."""
    fake = Mock(spec=httpx.Client)
    err = httpx.ConnectError("simulated network failure")
    fake.get.side_effect = err
    fake.post.side_effect = err
    fake.delete.side_effect = err
    app = cast(FastAPI, client.app)
    app.dependency_overrides[get_config_api_client] = lambda: fake


def test_create_test_dcm_unreachable(
    client: TestClient,
    create_device: DeviceFactory,
    create_environment: EnvironmentFactory,
) -> None:
    """POST /tests returns 503 with clear message when DCM is unreachable."""
    _, pc = create_device(name="PC 503", category="pc")
    _, rig = create_device(name="Rig 503", category="test_rig")
    _, env = create_environment(name="Env 503")

    _break_dcm(client)

    response = client.post(
        "/api/v1/tests",
        json={
            "experiment_id": "exp-503",
            "pc_device_id": pc["device_id"],
            "test_rig_device_id": rig["device_id"],
            "environment_id": env["environment_id"],
            "driver": "Driver",
            "requirements": "",
        },
    )

    assert response.status_code == 503
    assert "Configuration service unavailable" in response.json()["detail"]


def test_update_test_dcm_unreachable(
    create_test: TestFactory,
    client: TestClient,
) -> None:
    """PUT /tests/{id} returns 503 when DCM is unreachable and leaves Mongo unchanged."""
    _, test = create_test()
    original_driver = test["driver"]

    _break_dcm(client)

    response = client.put(
        f"/api/v1/tests/{test['test_id']}",
        json={"driver": "New Driver"},
    )

    assert response.status_code == 503
    assert "Configuration service unavailable" in response.json()["detail"]

    # Mongo must not have been touched — the driver is still the original value.
    refreshed = client.get(f"/api/v1/tests/{test['test_id']}").json()
    assert refreshed["driver"] == original_driver


def test_activate_test_dcm_unreachable(
    create_test: TestFactory,
    client: TestClient,
) -> None:
    """POST /tests/{id}/activate returns 503 when DCM is unreachable."""
    _, test = create_test()

    _break_dcm(client)

    response = client.post(f"/api/v1/tests/{test['test_id']}/activate")

    assert response.status_code == 503
    assert "Configuration service unavailable" in response.json()["detail"]


def test_telemetry_params_does_not_touch_dcm(
    create_test: TestFactory,
    client: TestClient,
) -> None:
    """GET /tests/{id}/telemetry-params works without calling DCM.

    The endpoint derives partition values from Mongo directly — if DCM is down
    it should still succeed because we never call it.
    """
    _, test = create_test()

    _break_dcm(client)

    response = client.get(f"/api/v1/tests/{test['test_id']}/telemetry-params")

    assert response.status_code == 200
    data = response.json()
    assert "environment" in data
    assert "test_rig" in data
    assert "experiment" in data
    assert "driver" in data


def test_delete_test_dcm_unreachable_keeps_mongo(
    create_test: TestFactory,
    client: TestClient,
) -> None:
    """DELETE /tests/{id} returns 503 when DCM is unreachable.

    Strict behavior: we refuse to delete from Mongo if we can't also clean up
    DCM — orphan DCM versions could be picked up by the AC bridge and enrich
    future telemetry with deleted-test content.
    """
    _, test = create_test()

    _break_dcm(client)

    response = client.delete(f"/api/v1/tests/{test['test_id']}")
    assert response.status_code == 503
    assert "Configuration service unavailable" in response.json()["detail"]

    # Verify test is STILL in Mongo (not deleted)
    get_response = client.get(f"/api/v1/tests/{test['test_id']}")
    assert get_response.status_code == 200
