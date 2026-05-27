import re

from fastapi.testclient import TestClient

from tests.conftest import DriverFactory

UTC_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ============================================================================
# Create
# ============================================================================


def test_create_driver(client: TestClient) -> None:
    """Test creating a driver with auto-generated ID."""
    response = client.post("/api/v1/drivers", json={"name": "Tomas"})
    assert response.status_code == 200
    data = response.json()
    assert data["driver_id"] == "DRV-0001"
    assert data["name"] == "Tomas"
    assert UTC_ISO_DATETIME.match(data["created_at"])


def test_create_driver_auto_increment(client: TestClient) -> None:
    """Test that driver IDs auto-increment."""
    client.post("/api/v1/drivers", json={"name": "Driver A"})
    response = client.post("/api/v1/drivers", json={"name": "Driver B"})
    assert response.json()["driver_id"] == "DRV-0002"


# ============================================================================
# List
# ============================================================================


def test_list_drivers_empty(client: TestClient) -> None:
    """Test empty list."""
    response = client.get("/api/v1/drivers")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_list_drivers_with_data(
    create_driver: DriverFactory, client: TestClient
) -> None:
    """Test listing and filtering drivers."""
    create_driver(name="Alice")
    create_driver(name="Bob")

    response = client.get("/api/v1/drivers")
    assert response.json()["total"] == 2

    # Filter by name
    response = client.get("/api/v1/drivers?name=Alice")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "Alice"


def test_list_drivers_text_search(
    create_driver: DriverFactory, client: TestClient
) -> None:
    """Test text search."""
    create_driver(name="Daniel Lastic")
    create_driver(name="Tomas Nekvinda")

    response = client.get("/api/v1/drivers?q=lastic")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "Daniel Lastic"


# ============================================================================
# Get / Update / Delete
# ============================================================================


def test_get_driver(create_driver: DriverFactory, client: TestClient) -> None:
    """Test getting a single driver."""
    _, created = create_driver(name="Tomas")
    response = client.get(f"/api/v1/drivers/{created['driver_id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "Tomas"


def test_get_driver_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/drivers/nonexistent")
    assert response.status_code == 404


def test_update_driver(create_driver: DriverFactory, client: TestClient) -> None:
    """Test updating a driver's name."""
    _, created = create_driver(name="Old Name")
    driver_id = created["driver_id"]

    response = client.put(f"/api/v1/drivers/{driver_id}", json={"name": "New Name"})
    assert response.status_code == 200
    assert response.json()["name"] == "New Name"

    # Verify persisted
    assert client.get(f"/api/v1/drivers/{driver_id}").json()["name"] == "New Name"


def test_update_driver_not_found(client: TestClient) -> None:
    response = client.put("/api/v1/drivers/nonexistent", json={"name": "X"})
    assert response.status_code == 404


def test_update_driver_no_fields(
    create_driver: DriverFactory, client: TestClient
) -> None:
    _, created = create_driver()
    response = client.put(f"/api/v1/drivers/{created['driver_id']}", json={})
    assert response.status_code == 400


def test_delete_driver(create_driver: DriverFactory, client: TestClient) -> None:
    """Test deleting a driver."""
    _, created = create_driver()
    driver_id = created["driver_id"]

    response = client.delete(f"/api/v1/drivers/{driver_id}")
    assert response.status_code == 200

    assert client.get(f"/api/v1/drivers/{driver_id}").status_code == 404


def test_delete_driver_not_found(client: TestClient) -> None:
    response = client.delete("/api/v1/drivers/nonexistent")
    assert response.status_code == 404
