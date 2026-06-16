import re
from typing import Any, Callable

from fastapi.testclient import TestClient

from tests.conftest import DeviceFactory

UTC_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ============================================================================
# Create
# ============================================================================


def test_create_device(client: TestClient) -> None:
    """Test that a Device can be created with auto-generated ID."""
    input_data = {
        "category": "pc",
        "name": "Daniel XPS",
        "status": "active",
    }
    response = client.post("/api/v1/devices", json=input_data)
    assert response.status_code == 200

    data = response.json()
    assert data["device_id"] == "DEV-0001"
    assert data["category"] == "pc"
    assert data["name"] == "Daniel XPS"
    assert data["status"] == "active"
    assert UTC_ISO_DATETIME.match(data["created_at"])
    assert UTC_ISO_DATETIME.match(data["updated_at"])


def test_create_device_test_rig(client: TestClient) -> None:
    """Test creating a test rig device."""
    input_data = {
        "category": "test_rig",
        "name": "Logitech G29",
    }
    response = client.post("/api/v1/devices", json=input_data)
    assert response.status_code == 200

    data = response.json()
    assert data["category"] == "test_rig"
    assert data["name"] == "Logitech G29"
    assert data["status"] == "active"  # default


def test_create_device_auto_increment_ids(client: TestClient) -> None:
    """Test that device IDs auto-increment."""
    client.post("/api/v1/devices", json={"category": "pc", "name": "PC1"})
    client.post("/api/v1/devices", json={"category": "pc", "name": "PC2"})
    response = client.post("/api/v1/devices", json={"category": "pc", "name": "PC3"})
    assert response.status_code == 200
    assert response.json()["device_id"] == "DEV-0003"


# ============================================================================
# List
# ============================================================================


def test_list_devices_empty(client: TestClient) -> None:
    """Test that an empty paginated response is returned when no devices exist."""
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total_pages"] == 0


def test_list_devices_with_data(
    create_device: DeviceFactory, client: TestClient
) -> None:
    """Test listing devices with filtering."""
    create_device(name="PC Alpha", category="pc")
    create_device(name="Fanatec DD Pro", category="test_rig")

    # All devices
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    assert response.json()["total"] == 2

    # Filter by category
    response = client.get("/api/v1/devices?category=pc")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "PC Alpha"

    # Filter by status
    response = client.get("/api/v1/devices?status=active")
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_list_devices_text_search(
    create_device: DeviceFactory, client: TestClient
) -> None:
    """Test text search with q parameter."""
    create_device(name="Daniel XPS", category="pc")
    create_device(name="Logitech G29", category="test_rig")

    response = client.get("/api/v1/devices?q=logitech")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Logitech G29"


# ============================================================================
# Get
# ============================================================================


def test_get_device(create_device: DeviceFactory, client: TestClient) -> None:
    """Test retrieving a single device by ID."""
    _, created = create_device(name="My PC")
    device_id = created["device_id"]

    response = client.get(f"/api/v1/devices/{device_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "My PC"


def test_get_device_not_found(client: TestClient) -> None:
    """Test that getting a non-existent device returns 404."""
    response = client.get("/api/v1/devices/nonexistent")
    assert response.status_code == 404
    assert response.json()["detail"] == "Device not found"


def test_get_devices_batch(create_device: DeviceFactory, client: TestClient) -> None:
    """Test batch retrieval of devices."""
    _, d1 = create_device(name="PC1")
    _, d2 = create_device(name="PC2")
    create_device(name="PC3")  # not requested

    response = client.post(
        "/api/v1/devices/batch",
        json=[d1["device_id"], d2["device_id"]],
    )
    assert response.status_code == 200
    names = {d["name"] for d in response.json()}
    assert names == {"PC1", "PC2"}


# ============================================================================
# Update
# ============================================================================


def test_update_device(create_device: DeviceFactory, client: TestClient) -> None:
    """Test updating a device."""
    _, created = create_device(name="Old Name", category="pc")
    device_id = created["device_id"]

    response = client.put(
        f"/api/v1/devices/{device_id}",
        json={"name": "New Name", "status": "inactive"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Name"
    assert data["status"] == "inactive"
    assert data["category"] == "pc"  # unchanged

    # Verify persisted
    response = client.get(f"/api/v1/devices/{device_id}")
    assert response.json()["name"] == "New Name"


def test_update_device_not_found(client: TestClient) -> None:
    """Test that updating a non-existent device returns 404."""
    response = client.put("/api/v1/devices/nonexistent", json={"name": "X"})
    assert response.status_code == 404


def test_update_device_no_fields(
    create_device: DeviceFactory, client: TestClient
) -> None:
    """Test that updating with no fields returns 400."""
    _, created = create_device()
    response = client.put(f"/api/v1/devices/{created['device_id']}", json={})
    assert response.status_code == 400


# ============================================================================
# Delete
# ============================================================================


def test_delete_device(create_device: DeviceFactory, client: TestClient) -> None:
    """Test deleting a device."""
    _, created = create_device()
    device_id = created["device_id"]

    response = client.delete(f"/api/v1/devices/{device_id}")
    assert response.status_code == 200

    # Verify gone
    response = client.get(f"/api/v1/devices/{device_id}")
    assert response.status_code == 404


def test_delete_device_not_found(client: TestClient) -> None:
    """Test that deleting a non-existent device returns 404."""
    response = client.delete("/api/v1/devices/nonexistent")
    assert response.status_code == 404


def test_delete_device_referenced_by_test(
    create_device: DeviceFactory,
    client: TestClient,
    create_test: Callable[..., Any],
) -> None:
    """Test that deleting a device referenced by a test returns 409."""
    _, pc = create_device(name="Referenced PC", category="pc")
    _, rig = create_device(name="Test Rig", category="test_rig")

    # Create environment for the test
    env_resp = client.post("/api/v1/environments", json={"name": "Env"})
    env_id = env_resp.json()["environment_id"]

    # Create a test referencing the PC device
    client.post(
        "/api/v1/tests",
        json={
            "experiment_id": "exp1",
            "pc_device_id": pc["device_id"],
            "test_rig_device_id": rig["device_id"],
            "environment_id": env_id,
            "driver": "Tomas",
        },
    )

    # Attempt to delete referenced device
    response = client.delete(f"/api/v1/devices/{pc['device_id']}")
    assert response.status_code == 409
    assert "Cannot delete" in response.json()["detail"]
    assert "referenced by test(s)" in response.json()["detail"]

    # Device should still exist
    response = client.get(f"/api/v1/devices/{pc['device_id']}")
    assert response.status_code == 200


# ============================================================================
# Pagination
# ============================================================================


def test_pagination_default(create_device: DeviceFactory, client: TestClient) -> None:
    """Test default pagination parameters."""
    for i in range(5):
        create_device(name=f"Device {i}")

    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 5
    assert data["total_pages"] == 1
    assert len(data["items"]) == 5


def test_pagination_multiple_pages(
    create_device: DeviceFactory, client: TestClient
) -> None:
    """Test pagination across multiple pages."""
    for i in range(25):
        create_device(name=f"Device {i:03d}")

    # Page 1
    response = client.get("/api/v1/devices?page=1&page_size=10")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 25
    assert data["total_pages"] == 3
    assert len(data["items"]) == 10

    # Page 3 (last, partial)
    response = client.get("/api/v1/devices?page=3&page_size=10")
    assert response.status_code == 200
    assert len(response.json()["items"]) == 5


def test_pagination_beyond_total(
    create_device: DeviceFactory, client: TestClient
) -> None:
    """Test requesting a page beyond available data."""
    for i in range(3):
        create_device(name=f"Device {i}")

    response = client.get("/api/v1/devices?page=10&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert len(data["items"]) == 0


def test_pagination_with_filtering(
    create_device: DeviceFactory, client: TestClient
) -> None:
    """Test pagination works correctly with filtering."""
    for i in range(8):
        create_device(name=f"PC {i}", category="pc")
    for i in range(4):
        create_device(name=f"Rig {i}", category="test_rig")

    response = client.get("/api/v1/devices?category=pc&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 8
    assert all(item["category"] == "pc" for item in data["items"])

    response = client.get("/api/v1/devices?category=test_rig&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 4
    assert all(item["category"] == "test_rig" for item in data["items"])
