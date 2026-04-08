from datetime import datetime, timezone
import re
from typing import Any, Callable

from fastapi.testclient import TestClient

UTC_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

DACFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]


def test_create_dac(client: TestClient) -> None:
    """Test that a Device can be successfully created via the POST endpoint."""
    input_data = {
        "device_id": "dac1",
        "manufacturer": "Acme Corp",
        "product_category": "WP",
        "product_name": "Vitocal 200-S",
        "product_type": "AWO-M-E-AC",
        "product_variant": "201.D10",
        "product_key": "7703",
        "sample_type": "PFP",
        "sample_nr": "42",
        "location": "Bench 3",
        "status": "created",
        "creator": "John Doe",
    }

    response = client.post("/api/v1/devices", json=input_data)
    if response.status_code != 200:
        print(f"Response status: {response.status_code}")
        print(f"Response body: {response.text}")
    assert response.status_code == 200

    output_data = response.json()

    # Verify basic fields
    assert output_data["device_id"] == input_data["device_id"]
    assert output_data["manufacturer"] == input_data["manufacturer"]
    assert output_data["product_category"] == input_data["product_category"]
    assert output_data["product_name"] == input_data["product_name"]
    assert output_data["sample_type"] == input_data["sample_type"]
    assert output_data["sample_nr"] == input_data["sample_nr"]
    assert output_data["sample_id"] == "PFP-42"  # Derived field
    assert output_data["location"] == input_data["location"]
    assert output_data["status"] == input_data["status"]
    assert output_data["creator"] == input_data["creator"]
    assert output_data["last_editor"] == input_data["creator"]
    assert UTC_ISO_DATETIME.match(output_data["created_at"])
    assert UTC_ISO_DATETIME.match(output_data["updated_at"])


def test_create_dac_derives_sample_id_without_nr(client: TestClient) -> None:
    """Test that sample_id is correctly derived when sample_nr is not provided."""
    input_data = {
        "device_id": "dac2",
        "manufacturer": "Acme Corp",
        "product_category": "Gas",
        "product_name": "Vitodens 200-W",
        "sample_type": "FP",
        "sample_nr": None,
        "location": "Lab 2",
        "creator": "Jane Smith",
    }

    response = client.post("/api/v1/devices", json=input_data)
    assert response.status_code == 200

    output_data = response.json()
    assert output_data["sample_id"] == "FP"  # Without sample_nr


def test_create_dac_duplicate_id(client: TestClient) -> None:
    """Test that creating a Device with duplicate device_id returns 409 Conflict."""
    input_data = {
        "device_id": "dac1",
        "manufacturer": "Acme Corp",
        "product_category": "WP",
        "product_name": "Vitocal 200-S",
        "sample_type": "PFP",
        "location": "Bench 3",
        "creator": "John Doe",
    }

    # Create first Device
    response = client.post("/api/v1/devices", json=input_data)
    assert response.status_code == 200

    # Attempt to create duplicate
    response = client.post("/api/v1/devices", json=input_data)
    assert response.status_code == 409
    assert response.json()["detail"] == "Device with this ID already exists"



def test_create_dac_creates_journal_entry(client: TestClient) -> None:
    """Test that creating a Device automatically creates an initial journal entry."""
    input_data = {
        "device_id": "dac4",
        "manufacturer": "Acme Corp",
        "product_category": "WP",
        "product_name": "Vitocal 200-S",
        "sample_type": "PFP",
        "location": "Bench 3",
        "creator": "John Doe",
    }

    # Create Device
    response = client.post("/api/v1/devices", json=input_data)
    assert response.status_code == 200

    # Get journal entries
    response = client.get("/api/v1/devices/dac4/journal")
    assert response.status_code == 200

    journal_entries = response.json()
    assert len(journal_entries) == 1
    assert journal_entries[0]["text"] == "Device created: PFP"  # Now includes sample_id
    assert journal_entries[0]["category"] == "Setup"
    assert journal_entries[0]["editor"] == "John Doe"
    assert "data" in journal_entries[0]


def test_list_dacs_empty(client: TestClient) -> None:
    """Test that an empty paginated response is returned when no DACs exist."""
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total_pages"] == 0


def test_list_dacs_with_data(client: TestClient) -> None:
    """Test that a paginated list of DACs is returned correctly."""
    # Create two DACs
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac2",
            "manufacturer": "Bosch",
            "product_category": "Gas",
            "product_name": "Condens 9000i",
            "sample_type": "FP",
            "location": "Lab 2",
            "creator": "Jane Smith",
        },
    )

    # Get all DACs
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Test filtering by manufacturer
    response = client.get("/api/v1/devices?manufacturer=Acme Corp")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["manufacturer"] == "Acme Corp"

    # Test filtering by status
    response = client.get("/api/v1/devices?status=created")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


def test_get_dac(client: TestClient) -> None:
    """Test retrieving a single Device by device_id."""
    # Create Device
    input_data = {
        "device_id": "dac1",
        "manufacturer": "Acme Corp",
        "product_category": "WP",
        "product_name": "Vitocal 200-S",
        "sample_type": "PFP",
        "location": "Bench 3",
        "creator": "John Doe",
    }
    client.post("/api/v1/devices", json=input_data)

    # Get Device
    response = client.get("/api/v1/devices/dac1")
    assert response.status_code == 200

    output_data = response.json()
    assert output_data["device_id"] == "dac1"
    assert output_data["manufacturer"] == "Acme Corp"


def test_get_dac_not_found(client: TestClient) -> None:
    """Test that getting a non-existent Device returns 404."""
    response = client.get("/api/v1/devices/nonexistent")
    assert response.status_code == 404
    assert response.json()["detail"] == "Device not found"


def test_update_dac(client: TestClient) -> None:
    """Test updating a Device."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Update Device
    update_data = {
        "status": "setup",
        "status_note": "Ready for testing",
        "location": "Lab 5",
        "last_editor": "Jane Smith",
    }

    response = client.put("/api/v1/devices/dac1", json=update_data)
    assert response.status_code == 200

    output_data = response.json()
    assert output_data["status"] == "setup"
    assert output_data["status_note"] == "Ready for testing"
    assert output_data["location"] == "Lab 5"
    assert output_data["last_editor"] == "Jane Smith"

    # Verify journal entry was created
    response = client.get("/api/v1/devices/dac1/journal")
    journal_entries = response.json()
    assert len(journal_entries) == 2  # Initial + update
    assert journal_entries[0]["text"] == "Device updated"  # Newest first


def test_update_dac_not_found(client: TestClient) -> None:
    """Test that updating a non-existent Device returns 404."""
    response = client.put(
        "/api/v1/devices/nonexistent", json={"status": "setup"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Device not found"


def test_update_dac_requires_at_least_one_field(client: TestClient) -> None:
    """Test that updating a Device requires at least one field."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Try to update with no fields
    response = client.put("/api/v1/devices/dac1", json={})
    assert response.status_code == 400
    assert "at least one field" in response.json()["detail"].lower()


def test_update_dac_recalculates_sample_id(client: TestClient) -> None:
    """Test that sample_id is recalculated when sample_type or sample_nr changes."""
    # Create Device with sample_nr
    response = client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "sample_nr": "42",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )
    assert response.status_code == 200
    assert response.json()["sample_id"] == "PFP-42"

    # Update sample_type - should recalculate sample_id
    response = client.put(
        "/api/v1/devices/dac1",
        json={
            "sample_type": "EVT",
            "last_editor": "Jane Smith",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sample_type"] == "EVT"
    assert data["sample_nr"] == "42"
    assert data["sample_id"] == "EVT-42"  # Should be recalculated

    # Update sample_nr - should recalculate sample_id
    response = client.put(
        "/api/v1/devices/dac1",
        json={
            "sample_nr": "99",
            "last_editor": "Jane Smith",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sample_type"] == "EVT"
    assert data["sample_nr"] == "99"
    assert data["sample_id"] == "EVT-99"  # Should be recalculated


def test_update_dac_filters_null_values(client: TestClient) -> None:
    """Test that None values in update don't overwrite existing data."""
    # Create Device with all fields populated
    response = client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "project": "Project A",
            "comment": "Initial comment",
            "creator": "John Doe",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["location"] == "Bench 3"
    assert data["project"] == "Project A"
    assert data["comment"] == "Initial comment"

    # Update with None values for location and project - should be filtered out
    response = client.put(
        "/api/v1/devices/dac1",
        json={
            "location": None,  # Should be ignored
            "project": None,  # Should be ignored
            "comment": "Updated comment",  # Should be updated
            "last_editor": "Jane Smith",
        },
    )
    assert response.status_code == 200
    data = response.json()

    # Verify that None values didn't overwrite existing data
    assert data["location"] == "Bench 3"  # Should remain unchanged
    assert data["project"] == "Project A"  # Should remain unchanged
    assert data["comment"] == "Updated comment"  # Should be updated
    assert data["last_editor"] == "Jane Smith"


def test_delete_dac(client: TestClient) -> None:
    """Test deleting a Device and its journal entries."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Update it to create another journal entry
    client.put("/api/v1/devices/dac1", json={"status": "setup"})

    # Delete Device
    response = client.delete("/api/v1/devices/dac1")
    assert response.status_code == 204

    # Verify Device is deleted
    response = client.get("/api/v1/devices/dac1")
    assert response.status_code == 404

    # Verify journal entries are deleted
    response = client.get("/api/v1/devices/dac1/journal")
    assert response.status_code == 404


def test_delete_dac_not_found(client: TestClient) -> None:
    """Test that deleting a non-existent Device returns 404."""
    response = client.delete("/api/v1/devices/nonexistent")
    assert response.status_code == 404
    assert response.json()["detail"] == "Device not found"


def test_delete_dac_referenced_by_test(client: TestClient) -> None:
    """Test that deleting a Device referenced by tests returns 409 Conflict."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "device-ref-test",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Create Test that references the Device
    client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-ref-device",
            "campaign_id": "campaign-001",
            "devices": [{"device_id": "device-ref-test"}],
            "environment_id": "env-001",
            "operator": "John Doe",
            "sensors": {},
        },
    )

    # Attempt to delete Device
    response = client.delete("/api/v1/devices/device-ref-test")
    assert response.status_code == 409
    error_detail = response.json()["detail"]
    assert "Cannot delete Device" in error_detail
    assert "Referenced by 1 test(s)" in error_detail
    assert "test-ref-device" in error_detail

    # Verify Device still exists
    response = client.get("/api/v1/devices/device-ref-test")
    assert response.status_code == 200


def test_delete_dac_referenced_by_multiple_tests(client: TestClient) -> None:
    """Test that error message shows multiple tests referencing Device."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "device-multi-ref",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Create multiple tests that reference the Device
    for i in range(3):
        client.post(
            "/api/v1/tests",
            json={
                "test_id": f"test-multi-{i}",
                "campaign_id": "campaign-001",
                "devices": [{"device_id": "device-multi-ref"}],
                "environment_id": "env-001",
                "operator": "John Doe",
                "sensors": {},
            },
        )

    # Attempt to delete Device
    response = client.delete("/api/v1/devices/device-multi-ref")
    assert response.status_code == 409
    error_detail = response.json()["detail"]
    assert "Cannot delete Device" in error_detail
    assert "Referenced by 3 test(s)" in error_detail


def test_get_dac_journal(client: TestClient) -> None:
    """Test getting journal entries for a Device."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Update to create more journal entries
    client.put("/api/v1/devices/dac1", json={"status": "setup"})
    client.put("/api/v1/devices/dac1", json={"status": "stored"})

    # Get journal
    response = client.get("/api/v1/devices/dac1/journal")
    assert response.status_code == 200

    journal_entries = response.json()
    assert len(journal_entries) == 3  # Initial + 2 updates
    # Verify sorted by timestamp descending (newest first)
    assert journal_entries[0]["text"] == "Device updated"
    assert journal_entries[0]["data"]["status"] == "stored"
    assert journal_entries[1]["data"]["status"] == "setup"
    assert journal_entries[2]["text"] == "Device created: PFP"  # Now includes sample_id


def test_get_dac_journal_not_found(client: TestClient) -> None:
    """Test that getting journal for non-existent Device returns 404."""
    response = client.get("/api/v1/devices/nonexistent/journal")
    assert response.status_code == 404
    assert response.json()["detail"] == "Device not found"


def test_create_manual_journal_entry(client: TestClient) -> None:
    """Test creating a manual journal entry for a Device."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Create manual journal entry
    journal_data = {
        "device_id": "dac1",
        "editor": "Jane Smith",
        "category": "Testing",
        "text": "Performed leak test - all OK",
        "data": {},  # Will be populated by server with current Device state
    }

    response = client.post("/api/v1/devices/dac1/journal", json=journal_data)
    assert response.status_code == 200

    output_data = response.json()
    assert output_data["text"] == "Performed leak test - all OK"
    assert output_data["category"] == "Testing"
    assert output_data["editor"] == "Jane Smith"
    assert "device_version" in output_data
    # Contains Device snapshot - check for _id field (raw DB format)
    assert output_data["data"]["_id"] == "dac1" or output_data["data"].get("device_id") == "dac1"

    # Verify it appears in journal list
    response = client.get("/api/v1/devices/dac1/journal")
    journal_entries = response.json()
    assert len(journal_entries) == 2  # Initial + manual
    assert journal_entries[0]["text"] == "Performed leak test - all OK"


def test_create_manual_journal_entry_not_found(client: TestClient) -> None:
    """Test that creating journal entry for non-existent Device returns 404."""
    journal_data = {
        "device_id": "nonexistent",
        "editor": "Jane Smith",
        "text": "Some note",
        "data": {},
    }

    response = client.post("/api/v1/devices/nonexistent/journal", json=journal_data)
    assert response.status_code == 404
    assert response.json()["detail"] == "Device not found"


# ============================================================================
# Change Detection Tests - generate_change_description()
# ============================================================================


def test_change_detection_single_field() -> None:
    """Test change detection for a single field update."""
    from api.routes.devices import generate_change_description

    old_dac = {
        "_id": "dac1",
        "status": "created",
        "location": "Bench 3",
    }

    new_data = {"status": "setup"}

    description, changed_fields = generate_change_description(old_dac, new_data)

    assert description == "Updated: Status (created → setup)"
    assert changed_fields == ["status"]


def test_change_detection_multiple_fields() -> None:
    """Test change detection for multiple field updates."""
    from api.routes.devices import generate_change_description

    old_dac = {
        "_id": "dac1",
        "status": "created",
        "location": "Bench 3",
        "project": None,
    }

    new_data = {"status": "setup", "location": "Lab 5", "project": "Project X"}

    description, changed_fields = generate_change_description(old_dac, new_data)

    assert "Status (created → setup)" in description
    assert "Location (Bench 3 → Lab 5)" in description
    assert "Project (None → Project X)" in description
    assert set(changed_fields) == {"status", "location", "project"}


def test_change_detection_no_changes() -> None:
    """Test change detection when no actual changes are made."""
    from api.routes.devices import generate_change_description

    old_dac = {
        "_id": "dac1",
        "status": "created",
        "location": "Bench 3",
    }

    new_data = {"status": "created"}  # Same value

    description, changed_fields = generate_change_description(old_dac, new_data)

    assert description == "No significant changes detected"
    assert changed_fields == []




def test_change_detection_none_to_value() -> None:
    """Test change detection when fields go from None to a value."""
    from api.routes.devices import generate_change_description

    old_dac = {
        "_id": "dac1",
        "status_note": None,
        "comment": None,
    }

    new_data = {"status_note": "Ready for testing", "comment": "New sample"}

    description, changed_fields = generate_change_description(old_dac, new_data)

    assert "Status Note (None → Ready for testing)" in description
    assert "Comment (None → New sample)" in description
    assert set(changed_fields) == {"status_note", "comment"}


def test_change_detection_value_to_none() -> None:
    """Test change detection when fields go from a value to None."""
    from api.routes.devices import generate_change_description

    old_dac = {
        "_id": "dac1",
        "project": "Project X",
        "comment": "Some comment",
    }

    new_data = {"project": None, "comment": None}

    description, changed_fields = generate_change_description(old_dac, new_data)

    assert "Project (Project X → None)" in description
    assert "Comment (Some comment → None)" in description
    assert set(changed_fields) == {"project", "comment"}



def test_preview_update_endpoint(client: TestClient) -> None:
    """Test the preview-update endpoint integration."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "status": "created",
            "creator": "John Doe",
        },
    )

    # Preview an update
    update_data = {"status": "setup", "location": "Lab 5"}

    response = client.post("/api/v1/devices/dac1/preview-update", json=update_data)
    assert response.status_code == 200

    preview_data = response.json()
    assert "suggested_text" in preview_data
    assert "changed_fields" in preview_data

    # Verify suggested text contains the changes
    # Note: DeviceStatus enum may be serialized as "DeviceStatus.SETUP" or "setup"
    suggested = preview_data["suggested_text"]
    assert "Status" in suggested and ("setup" in suggested.lower() or "SETUP" in suggested)
    assert "Location (Bench 3 → Lab 5)" in suggested
    assert set(preview_data["changed_fields"]) == {"status", "location"}


def test_preview_update_no_changes(client: TestClient) -> None:
    """Test preview endpoint when no changes are made."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "status": "created",
            "creator": "John Doe",
        },
    )

    # Preview with same values
    update_data = {"status": "created"}

    response = client.post("/api/v1/devices/dac1/preview-update", json=update_data)
    assert response.status_code == 200

    preview_data = response.json()
    assert preview_data["suggested_text"] == "No significant changes detected"
    assert preview_data["changed_fields"] == []


def test_preview_update_not_found(client: TestClient) -> None:
    """Test preview endpoint for non-existent Device."""
    update_data = {"status": "setup"}

    response = client.post("/api/v1/devices/nonexistent/preview-update", json=update_data)
    assert response.status_code == 404
    assert response.json()["detail"] == "Device not found"


def test_create_dac_with_custom_journal_text(client: TestClient) -> None:
    """Test creating a Device with custom journal text."""
    input_data = {
        "device_id": "dac1",
        "manufacturer": "Acme Corp",
        "product_category": "WP",
        "product_name": "Vitocal 200-S",
        "sample_type": "PFP",
        "location": "Bench 3",
        "creator": "John Doe",
        "journal_text": "Custom creation note: Initial setup complete",
        "journal_category": "Setup",
    }

    response = client.post("/api/v1/devices", json=input_data)
    assert response.status_code == 200

    # Verify journal entry has custom text
    response = client.get("/api/v1/devices/dac1/journal")
    journal_entries = response.json()
    assert len(journal_entries) == 1
    assert journal_entries[0]["text"] == "Custom creation note: Initial setup complete"
    assert journal_entries[0]["category"] == "Setup"


def test_update_dac_with_custom_journal_text(client: TestClient) -> None:
    """Test updating a Device with custom journal text."""
    # Create Device
    client.post(
        "/api/v1/devices",
        json={
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "location": "Bench 3",
            "creator": "John Doe",
        },
    )

    # Update with custom journal text
    update_data = {
        "status": "setup",
        "journal_text": "Manual note: Configured for testing",
        "journal_category": "Testing",
    }

    response = client.put("/api/v1/devices/dac1", json=update_data)
    assert response.status_code == 200

    # Verify journal entry has custom text
    response = client.get("/api/v1/devices/dac1/journal")
    journal_entries = response.json()
    assert len(journal_entries) == 2
    assert journal_entries[0]["text"] == "Manual note: Configured for testing"
    assert journal_entries[0]["category"] == "Testing"


# ============================================================================
# Pagination Tests
# ============================================================================


def test_dac_pagination_default_parameters(client: TestClient) -> None:
    """Test that pagination uses default parameters (page=1, page_size=20)."""
    # Create a few DACs
    for i in range(5):
        client.post(
            "/api/v1/devices",
            json={
                "device_id": f"device-{i}",
                "manufacturer": "Acme Corp",
                "product_category": "WP",
                "product_name": "Vitocal 200-S",
                "sample_type": "PFP",
                "location": "Bench 3",
                "creator": "John Doe",
            },
        )

    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    data = response.json()

    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 5
    assert data["total_pages"] == 1
    assert len(data["items"]) == 5


def test_dac_pagination_custom_page_size(client: TestClient) -> None:
    """Test pagination with custom page_size values (10, 20, 50, 100, 200)."""
    # Create 10 DACs
    for i in range(10):
        client.post(
            "/api/v1/devices",
            json={
                "device_id": f"device-{i}",
                "manufacturer": "Acme Corp",
                "product_category": "WP",
                "product_name": "Vitocal 200-S",
                "sample_type": "PFP",
                "location": "Bench 3",
                "creator": "John Doe",
            },
        )

    # Test page_size=20
    response = client.get("/api/v1/devices?page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page_size"] == 20
    assert data["total"] == 10
    assert data["total_pages"] == 1
    assert len(data["items"]) == 10

    # Test page_size=100
    response = client.get("/api/v1/devices?page_size=100")
    assert response.status_code == 200
    data = response.json()
    assert data["page_size"] == 100
    assert data["total"] == 10
    assert len(data["items"]) == 10


def test_dac_pagination_multiple_pages(client: TestClient) -> None:
    """Test pagination across multiple pages."""
    # Create 60 DACs
    for i in range(60):
        client.post(
            "/api/v1/devices",
            json={
                "device_id": f"device-{i:03d}",
                "manufacturer": "Acme Corp",
                "product_category": "WP",
                "product_name": "Vitocal 200-S",
                "sample_type": "PFP",
                "location": "Bench 3",
                "creator": "John Doe",
            },
        )

    # Get page 1 with page_size=20
    response = client.get("/api/v1/devices?page=1&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 60
    assert data["total_pages"] == 3
    assert len(data["items"]) == 20

    # Get page 2
    response = client.get("/api/v1/devices?page=2&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2
    assert data["total"] == 60
    assert data["total_pages"] == 3
    assert len(data["items"]) == 20

    # Get page 3 (last page)
    response = client.get("/api/v1/devices?page=3&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 3
    assert data["total"] == 60
    assert data["total_pages"] == 3
    assert len(data["items"]) == 20


def test_dac_pagination_page_beyond_total(client: TestClient) -> None:
    """Test requesting a page beyond the total number of pages."""
    # Create 5 DACs
    for i in range(5):
        client.post(
            "/api/v1/devices",
            json={
                "device_id": f"device-{i}",
                "manufacturer": "Acme Corp",
                "product_category": "WP",
                "product_name": "Vitocal 200-S",
                "sample_type": "PFP",
                "location": "Bench 3",
                "creator": "John Doe",
            },
        )

    # Request page 10 (beyond available data)
    response = client.get("/api/v1/devices?page=10&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 10
    assert data["total"] == 5
    assert data["total_pages"] == 1
    assert len(data["items"]) == 0  # Empty results


def test_dac_pagination_with_filtering(client: TestClient) -> None:
    """Test pagination works correctly with filtering."""
    # Create DACs with different manufacturers
    for i in range(15):
        manufacturer = "Acme Corp" if i < 10 else "Bosch"
        client.post(
            "/api/v1/devices",
            json={
                "device_id": f"device-{i:02d}",
                "manufacturer": manufacturer,
                "product_category": "WP" if manufacturer == "Acme Corp" else "Gas",
                "product_name": "Vitocal 200-S" if manufacturer == "Acme Corp" else "Condens 9000i",
                "sample_type": "PFP",
                "location": "Bench 3",
                "creator": "John Doe",
            },
        )

    # Filter by manufacturer=Acme Corp with page_size=20
    response = client.get("/api/v1/devices?manufacturer=Acme Corp&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 10
    assert data["total_pages"] == 1
    assert len(data["items"]) == 10
    assert all(item["manufacturer"] == "Acme Corp" for item in data["items"])

    # Filter by manufacturer=Bosch
    response = client.get("/api/v1/devices?manufacturer=Bosch&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert data["total_pages"] == 1
    assert len(data["items"]) == 5
    assert all(item["manufacturer"] == "Bosch" for item in data["items"])


def test_dac_pagination_page_size_larger_than_total(client: TestClient) -> None:
    """Test when page_size is larger than total number of items."""
    # Create 3 DACs
    for i in range(3):
        client.post(
            "/api/v1/devices",
            json={
                "device_id": f"device-{i}",
                "manufacturer": "Acme Corp",
                "product_category": "WP",
                "product_name": "Vitocal 200-S",
                "sample_type": "PFP",
                "location": "Bench 3",
                "creator": "John Doe",
            },
        )

    # Request with page_size=200 (larger than total)
    response = client.get("/api/v1/devices?page_size=200")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 200
    assert data["total"] == 3
    assert data["total_pages"] == 1
    assert len(data["items"]) == 3
