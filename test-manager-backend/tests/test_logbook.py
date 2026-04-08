from fastapi.testclient import TestClient
from influxdb import InfluxDBClient
from datetime import datetime, timezone, timedelta

from api.settings import get_settings
from tests.conftest import TestFactory


def test_create_logbook_entry_test_not_found(client: TestClient) -> None:
    logbook_entry_data = {
        "content": "This is a logbook entry.",
        "operator": "John Doe",
    }
    response = client.post(
        "/api/v1/tests/nonexistent_id/logbook",
        json=logbook_entry_data,
    )
    assert response.status_code == 404, response.json()


def test_create_logbook_entry(
    client: TestClient, create_test: TestFactory, influx: InfluxDBClient
) -> None:
    test_id = "test1"
    create_test(test_id=test_id)

    logbook_entry_data = {
        "operator": "John Doe",
        "content": "This is a logbook entry.",
        "sensor_ids": ["sensor1", "sensor2"],
        "timestamp": "2025-01-01T00:00:00Z",
    }
    response = client.post("/api/v1/tests/test1/logbook", json=logbook_entry_data)
    assert response.status_code == 200
    data = response.json()
    assert data["test_id"] == "test1"
    assert data["operator"] == "John Doe"
    assert data["content"] == "This is a logbook entry."
    assert data["sensor_ids"] == ["sensor1", "sensor2"]
    assert "id" in data
    assert "created_at" in data
    assert data["timestamp"] == "2025-01-01T00:00:00Z"

    # Verify that the entry is in InfluxDB
    settings = get_settings().influx
    query = f'SELECT * FROM "{settings.measurement}" WHERE "id" = \'{data["id"]}\''
    result = influx.query(query)
    points = list(result.get_points())
    assert len(points) == 1
    point = points[0]
    assert point["id"] == data["id"]
    assert point["time"] == "2025-01-01T00:00:00Z"
    assert point["test_id"] == "test1"
    assert point["content"] == "This is a logbook entry."
    assert point["operator"] == "John Doe"
    assert point["sensor_ids"] == "sensor1,sensor2"
    assert point["created_at"] == int(
        datetime.fromisoformat(data["created_at"]).timestamp()
    )


def test_create_logbook_entry_non_utc_timestamp_normalized(
    client: TestClient, create_test: TestFactory
) -> None:
    """
    Tests that a logbook entry with non-UTC timestamp gets normalized to UTC in the response.
    """
    # Create a test first
    test_id = "test_timestamp_norm"
    create_test(test_id=test_id)

    # Create a datetime with non-UTC timezone (EST: UTC-5)
    # 2025-01-01 12:00:00 EST = 2025-01-01 17:00:00 UTC
    est_timezone = timezone(timedelta(hours=-5))  # EST is UTC-5
    est_timestamp = datetime(2025, 1, 1, 12, 0, 0, tzinfo=est_timezone)

    # Calculate expected UTC timestamp using datetime operations
    expected_utc_timestamp = est_timestamp.astimezone(timezone.utc)
    expected_utc_string = expected_utc_timestamp.isoformat().replace("+00:00", "Z")

    logbook_entry_data = {
        "operator": "Jane Doe",
        "content": "Entry with non-UTC timestamp",
        "sensor_ids": ["sensor1"],
        "timestamp": est_timestamp.isoformat(),  # Pass as ISO string with timezone
    }

    response = client.post(
        "/api/v1/tests/test_timestamp_norm/logbook", json=logbook_entry_data
    )
    assert response.status_code == 200
    data = response.json()

    # Verify basic fields
    assert data["test_id"] == "test_timestamp_norm"
    assert data["operator"] == "Jane Doe"
    assert data["content"] == "Entry with non-UTC timestamp"
    assert data["sensor_ids"] == ["sensor1"]
    assert "id" in data
    assert "created_at" in data

    # Most importantly: verify the timestamp is normalized to UTC
    assert data["timestamp"] == expected_utc_string, (
        f"Expected {expected_utc_string}, got {data['timestamp']} - "
        f"Input EST {est_timestamp.isoformat()} should convert to UTC"
    )
    assert data["timestamp"] == "2025-01-01T17:00:00Z", (
        "Specific test: EST 12:00 should become UTC 17:00"
    )


def test_get_logbook_entries_empty(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_for_logbook"
    create_test(test_id=test_id)

    response = client.get("/api/v1/tests/test_for_logbook/logbook")
    assert response.status_code == 200
    assert response.json() == []


def test_get_logbook_entries(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_with_entries"
    create_test(test_id=test_id)

    # Create two logbook entries
    logbook_entry_1 = {
        "operator": "Alice",
        "content": "First entry.",
    }
    client.post("/api/v1/tests/test_with_entries/logbook", json=logbook_entry_1)

    logbook_entry_2 = {
        "operator": "Bob",
        "content": "Second entry.",
    }
    client.post("/api/v1/tests/test_with_entries/logbook", json=logbook_entry_2)

    response = client.get("/api/v1/tests/test_with_entries/logbook")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    # You might want to sort or have more specific checks here
    # For now, just check entries for correctness
    assert data[0]["content"] == "First entry."
    assert data[1]["content"] == "Second entry."


def test_get_logbook_entry(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_single_entry"
    create_test(test_id=test_id)

    # Create a logbook entry
    logbook_entry_data = {
        "operator": "Alice",
        "content": "Single entry to retrieve.",
        "sensor_ids": ["sensor1"],
    }
    response = client.post(
        "/api/v1/tests/test_single_entry/logbook", json=logbook_entry_data
    )
    assert response.status_code == 200
    entry_id = response.json()["id"]

    # Get the single entry
    response = client.get(f"/api/v1/tests/test_single_entry/logbook/{entry_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == entry_id
    assert data["test_id"] == "test_single_entry"
    assert data["operator"] == "Alice"
    assert data["content"] == "Single entry to retrieve."
    assert data["sensor_ids"] == ["sensor1"]


def test_get_logbook_entry_not_found(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_not_found"
    create_test(test_id=test_id)

    # Try to get a non-existent entry
    response = client.get("/api/v1/tests/test_not_found/logbook/nonexistent_id")
    assert response.status_code == 404


def test_get_logbook_entry_wrong_test_id(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_wrong_test"
    create_test(test_id=test_id)

    # Create a logbook entry
    logbook_entry_data = {
        "operator": "Alice",
        "content": "Entry in wrong test.",
    }
    response = client.post(
        "/api/v1/tests/test_wrong_test/logbook", json=logbook_entry_data
    )
    assert response.status_code == 200
    entry_id = response.json()["id"]

    # Try to get the entry with a different test_id
    response = client.get(f"/api/v1/tests/different_test/logbook/{entry_id}")
    assert response.status_code == 404


def test_update_logbook_entry(
    client: TestClient, create_test: TestFactory, influx: InfluxDBClient
) -> None:
    test_id = "test_for_update"
    create_test(test_id=test_id)
    logbook_entry = {"operator": "Charlie", "content": "Initial."}
    response = client.post("/api/v1/tests/test_for_update/logbook", json=logbook_entry)
    entry_id = response.json()["id"]

    update_data = {"content": "Updated content."}
    response = client.put(
        f"/api/v1/tests/test_for_update/logbook/{entry_id}", json=update_data
    )
    assert response.status_code == 200
    assert response.json()["content"] == "Updated content."

    # Verify that the entry is updated in InfluxDB
    settings = get_settings().influx
    query = f'SELECT * FROM "{settings.measurement}" WHERE "id" = \'{entry_id}\''
    result = influx.query(query)
    points = list(result.get_points())
    assert len(points) == 1
    assert points[0]["content"] == "Updated content."


def test_update_logbook_entry_not_found(client: TestClient) -> None:
    update_data = {"content": "ghost"}
    response = client.put(
        "/api/v1/tests/some_test/logbook/nonexistent_id", json=update_data
    )
    assert response.status_code == 404


def test_update_logbook_entry_no_data(client: TestClient) -> None:
    response = client.put("/api/v1/tests/some_test/logbook/some_id", json={})
    assert response.status_code == 400


def test_delete_logbook_entry(
    client: TestClient, create_test: TestFactory, influx: InfluxDBClient
) -> None:
    test_id = "test_for_delete"
    create_test(test_id=test_id)
    logbook_entry = {"operator": "Dave", "content": "To be deleted."}
    response = client.post("/api/v1/tests/test_for_delete/logbook", json=logbook_entry)
    entry_id = response.json()["id"]

    response = client.delete(f"/api/v1/tests/nonexistent_id/logbook/{entry_id}")
    assert response.status_code == 404

    response = client.delete("/api/v1/tests/test_for_delete/logbook/nonexistent_id")
    assert response.status_code == 404

    response = client.delete(f"/api/v1/tests/test_for_delete/logbook/{entry_id}")
    assert response.status_code == 204

    # Verify that the entry is also deleted from InfluxDB
    settings = get_settings().influx
    query = f'SELECT * FROM "{settings.measurement}" WHERE "id" = \'{entry_id}\''
    result = influx.query(query)
    assert not list(result.get_points())


def test_create_logbook_entry_with_default_timestamp(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_default_timestamp"
    create_test(test_id=test_id)

    # Create entry without providing timestamp - should default to current time
    logbook_entry_data = {
        "operator": "Eve",
        "content": "Entry with default timestamp.",
    }
    response = client.post(
        "/api/v1/tests/test_default_timestamp/logbook", json=logbook_entry_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Entry with default timestamp."
    assert "timestamp" in data
    # Check datetime format for generated default timestamp
    assert data["timestamp"].endswith("Z")
    assert "T" in data["timestamp"]


def test_create_logbook_entry_with_custom_timestamp(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_custom_timestamp"
    create_test(test_id=test_id)

    # Create entry with custom timestamp - should use provided timestamp
    logbook_entry_data = {
        "operator": "Frank",
        "content": "Entry with custom timestamp.",
        "timestamp": "2023-06-15T14:30:00Z",
    }
    response = client.post(
        "/api/v1/tests/test_custom_timestamp/logbook", json=logbook_entry_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Entry with custom timestamp."
    assert data["timestamp"] == "2023-06-15T14:30:00Z"


def test_update_logbook_entry_with_timestamp(
    client: TestClient, create_test: TestFactory, influx: InfluxDBClient
) -> None:
    test_id = "test_timestamp_update"
    create_test(test_id=test_id)

    logbook_entry = {"operator": "Grace", "content": "Initial entry."}
    response = client.post(
        "/api/v1/tests/test_timestamp_update/logbook", json=logbook_entry
    )
    entry_id = response.json()["id"]

    update_data = {
        "operator": "Grace Updated",
        "content": "Updated with timestamp.",
        "timestamp": "2023-12-25T18:00:00Z",
    }
    response = client.put(
        f"/api/v1/tests/test_timestamp_update/logbook/{entry_id}", json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["operator"] == "Grace Updated"
    assert data["content"] == "Updated with timestamp."
    assert data["timestamp"] == "2023-12-25T18:00:00Z"

    # Verify that the entry is updated in InfluxDB
    settings = get_settings().influx
    query = f'SELECT * FROM "{settings.measurement}" WHERE "id" = \'{entry_id}\''
    result = influx.query(query)
    points = list(result.get_points())
    assert len(points) == 1
    point = points[0]
    assert point["operator"] == "Grace Updated"
    assert point["content"] == "Updated with timestamp."


def test_update_logbook_entry_timestamp_only(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_timestamp_only"
    create_test(test_id=test_id)

    logbook_entry = {"operator": "Henry", "content": "Entry to update timestamp only."}
    response = client.post(
        "/api/v1/tests/test_timestamp_only/logbook", json=logbook_entry
    )
    entry_id = response.json()["id"]
    original_content = response.json()["content"]
    original_operator = response.json()["operator"]

    update_data = {"timestamp": "2023-08-10T09:15:00Z"}
    response = client.put(
        f"/api/v1/tests/test_timestamp_only/logbook/{entry_id}", json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["operator"] == original_operator  # Operator should remain unchanged
    assert data["content"] == original_content  # Content should remain unchanged
    assert data["timestamp"] == "2023-08-10T09:15:00Z"


def test_update_logbook_entry_operator_only(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_operator_only"
    create_test(test_id=test_id)

    logbook_entry = {
        "operator": "Original Operator",
        "content": "Entry to update operator only.",
    }
    response = client.post(
        "/api/v1/tests/test_operator_only/logbook", json=logbook_entry
    )
    entry_id = response.json()["id"]
    original_content = response.json()["content"]
    original_timestamp = response.json()["timestamp"]

    update_data = {"operator": "Updated Operator"}
    response = client.put(
        f"/api/v1/tests/test_operator_only/logbook/{entry_id}", json=update_data
    )
    assert response.status_code == 200
    data = response.json()
    assert data["operator"] == "Updated Operator"
    assert data["content"] == original_content  # Content should remain unchanged
    # Timestamp should remain mostly unchanged (allowing for microsecond precision differences)
    # Both timestamps should be in the same second
    assert data["timestamp"][:19] == original_timestamp[:19]  # Compare up to seconds
    assert data["timestamp"].endswith("Z")
    assert "T" in data["timestamp"]
