import re

from fastapi.testclient import TestClient

from tests.conftest import EnvironmentFactory

UTC_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ============================================================================
# Create
# ============================================================================


def test_create_environment(client: TestClient) -> None:
    """Test creating an environment with auto-generated ID."""
    response = client.post(
        "/api/v1/environments",
        json={
            "name": "Prague Office",
            "location": "Prague, CZ",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["environment_id"] == "ENV-0001"
    assert data["name"] == "Prague Office"
    assert data["location"] == "Prague, CZ"
    assert data["status"] == "active"  # default
    assert UTC_ISO_DATETIME.match(data["created_at"])


def test_create_environment_auto_increment(client: TestClient) -> None:
    """Test that environment IDs auto-increment."""
    client.post("/api/v1/environments", json={"name": "Env A"})
    response = client.post("/api/v1/environments", json={"name": "Env B"})
    assert response.json()["environment_id"] == "ENV-0002"


# ============================================================================
# List
# ============================================================================


def test_list_environments_empty(client: TestClient) -> None:
    response = client.get("/api/v1/environments")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_list_environments_with_data(
    create_environment: EnvironmentFactory, client: TestClient
) -> None:
    """Test listing and filtering environments."""
    create_environment(name="Prague Office", status="active")
    create_environment(name="Allach Lab", status="inactive")

    response = client.get("/api/v1/environments")
    assert response.json()["total"] == 2

    # Filter by status
    response = client.get("/api/v1/environments?status=active")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "Prague Office"


def test_list_environments_text_search(
    create_environment: EnvironmentFactory, client: TestClient
) -> None:
    """Test text search."""
    create_environment(name="Prague Office")
    create_environment(name="Allach Lab", location="Munich")

    response = client.get("/api/v1/environments?q=munich")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "Allach Lab"


# ============================================================================
# Get / Update / Delete
# ============================================================================


def test_get_environment(
    create_environment: EnvironmentFactory, client: TestClient
) -> None:
    _, created = create_environment(name="Test Env")
    response = client.get(f"/api/v1/environments/{created['environment_id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "Test Env"


def test_get_environment_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/environments/nonexistent")
    assert response.status_code == 404


def test_update_environment(
    create_environment: EnvironmentFactory, client: TestClient
) -> None:
    _, created = create_environment(name="Old Name")
    env_id = created["environment_id"]

    response = client.put(
        f"/api/v1/environments/{env_id}",
        json={
            "name": "New Name",
            "status": "inactive",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Name"
    assert data["status"] == "inactive"


def test_update_environment_not_found(client: TestClient) -> None:
    response = client.put("/api/v1/environments/nonexistent", json={"name": "X"})
    assert response.status_code == 404


def test_update_environment_no_fields(
    create_environment: EnvironmentFactory, client: TestClient
) -> None:
    _, created = create_environment()
    response = client.put(f"/api/v1/environments/{created['environment_id']}", json={})
    assert response.status_code == 400


def test_delete_environment(
    create_environment: EnvironmentFactory, client: TestClient
) -> None:
    _, created = create_environment()
    env_id = created["environment_id"]

    response = client.delete(f"/api/v1/environments/{env_id}")
    assert response.status_code == 200

    assert client.get(f"/api/v1/environments/{env_id}").status_code == 404


def test_delete_environment_not_found(client: TestClient) -> None:
    response = client.delete("/api/v1/environments/nonexistent")
    assert response.status_code == 404
