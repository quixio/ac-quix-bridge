from datetime import datetime, timezone
import re
from io import BytesIO
from typing import Any
from unittest.mock import Mock
from urllib.parse import urlparse

import httpx
from fastapi.testclient import TestClient
from influxdb import InfluxDBClient

from api.config_api import get_config_api_client
from api.settings import get_settings
from tests.conftest import TestFactory

UTC_ISO_DATATIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


def test_create_test(create_test: TestFactory, config_api: httpx.Client) -> None:
    """
    Tests that a test can be successfully created via the POST endpoint.
    """
    input_data, output_data = create_test()

    # Verify basic fields
    assert output_data["test_id"] == input_data["test_id"]
    assert output_data["campaign_id"] == input_data["campaign_id"]
    assert output_data["devices"] == input_data["devices"]
    assert output_data["environment_id"] == input_data["environment_id"]
    assert output_data["operator"] == input_data["operator"]
    assert output_data["sensors"] == input_data["sensors"]
    assert output_data["status"] == input_data["status"]
    assert output_data["start"] == input_data["start"].replace("+00:00", "Z")
    assert output_data["end"] == input_data["end"].replace("+00:00", "Z")
    assert output_data["config_id"] is not None
    assert output_data["links"] == []
    assert output_data["files"] == {}
    assert UTC_ISO_DATATIME.match(output_data["created_at"])
    assert UTC_ISO_DATATIME.match(output_data["updated_at"])

    # Verify that a configuration was created in the Config API using the stored config_id
    config_id = output_data["config_id"]
    url = f"/api/v1/configurations/{config_id}"
    config_metadata = config_api.get(url).json()
    config_content = config_api.get(f"{url}/content").json()
    assert config_metadata["data"]["metadata"]["target_key"] == "test1"
    assert config_metadata["data"]["metadata"]["type"] == "TestConfig"
    assert config_content["test_id"] == input_data["test_id"]
    assert config_content["campaign_id"] == input_data["campaign_id"]
    assert config_content["devices"] == input_data["devices"]
    assert config_content["environment_id"] == input_data["environment_id"]
    assert config_content["operator"] == input_data["operator"]
    assert config_content["sensors"] == input_data["sensors"]


def test_create_test_duplicate_id(create_test: TestFactory, client: TestClient) -> None:
    """
    Tests that creating a test with a duplicate test_id returns a 409 Conflict.
    """
    input_data, _ = create_test()

    # Attempt to create it again with the same test_id
    response = client.post("/api/v1/tests", json=input_data)
    assert response.status_code == 409
    assert response.json()["detail"] == "Test with this ID already exists"


def test_create_test_with_status(create_test: TestFactory) -> None:
    """
    Tests that a test can be created with a specific status.
    """
    _, output_data = create_test(status="in_progress")
    assert output_data["status"] == "in_progress"


def test_list_tests_empty(client: TestClient) -> None:
    """
    Tests that an empty paginated response is returned when no tests exist.
    """
    response = client.get("/api/v1/tests")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total_pages"] == 0


def test_list_tests_with_data(create_test: TestFactory, client: TestClient) -> None:
    """
    Tests that a paginated list of tests is returned correctly and that
    filtering by a query parameter works.
    """
    # 1. Insert some data directly into the test database
    _, output_data1 = create_test(test_id="test1", operator="John Doe")
    _, output_data2 = create_test(test_id="test2", operator="Jane Smith")

    # 2. Test getting all tests
    response = client.get("/api/v1/tests")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2

    # Sort by test_id to have a predictable order
    data["items"].sort(key=lambda x: x["test_id"])

    # Verify basic fields for both tests
    assert data["items"][0]["test_id"] == output_data1["test_id"]
    assert data["items"][1]["test_id"] == output_data2["test_id"]

    # 3. Test filtering by a specific parameter
    response = client.get("/api/v1/tests?operator=Jane%20Smith")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["test_id"] == output_data2["test_id"]

    # 4. Test filtering by status
    response = client.get("/api/v1/tests?status=draft")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


def test_get_test_not_found(client: TestClient) -> None:
    """
    Tests that a 404 is returned when requesting a test that does not exist.
    """
    response = client.get("/api/v1/tests/nonexistent_id")
    assert response.status_code == 404


def test_get_test_found(create_test: TestFactory, client: TestClient) -> None:
    """
    Tests that a single test can be retrieved successfully by its ID.
    """
    # 1. Create a test via the API
    input_data, _ = create_test(test_id="test123")

    # 2. Request that test via the API
    response = client.get("/api/v1/tests/test123")
    assert response.status_code == 200
    assert response.json()["test_id"] == input_data["test_id"]


def test_get_test_with_files_and_links(
    create_test: TestFactory, client: TestClient
) -> None:
    """
    Tests that a test with files and links is retrieved correctly.
    """
    # 1. Create a test
    test_id = "test_with_content"
    create_test(test_id=test_id)

    # 2. Add a link
    link_data = {"url": "http://example.com", "label": "Example"}
    response = client.post(f"/api/v1/tests/{test_id}/links", json=link_data)
    assert response.status_code == 200
    link = response.json()

    # 3. Upload a file
    response = client.post(
        f"/api/v1/tests/{test_id}/files",
        json={"filename": "test.txt"},
    )
    assert response.status_code == 200
    url = urlparse(response.json()["url"])
    upload_path = f"{url.path}?{url.query}"
    files = {"file": ("test.txt", BytesIO(b"This is a test file."), "text/plain")}
    response = client.post(upload_path, files=files)
    assert response.status_code == 200
    file_data = response.json()

    # 4. Get the test and verify content
    response = client.get("/api/v1/tests/test_with_content")
    assert response.status_code == 200
    test = response.json()

    assert len(test["links"]) == 1
    assert test["links"][0]["id"] == link["id"]
    assert test["links"][0]["url"] == link["url"]
    assert test["links"][0]["label"] == link["label"]

    assert len(test["files"]) == 1
    file_id = file_data["id"]
    assert file_id in test["files"]
    assert test["files"][file_id]["name"] == file_data["name"]
    assert test["files"][file_id]["url"] == file_data["url"]


def test_update_test(
    create_test: TestFactory, client: TestClient, config_api: httpx.Client, create_dac: Any
) -> None:
    """
    Tests updating a test's metadata.
    """
    # 1. Create a Device and test to be updated
    _, created_dac = create_dac(device_id="original_dac")
    original_dacs = [{"device_id": "original_dac", "device_version": None}]

    _, output_data = create_test(
        test_id="test_to_update",
        campaign_id="original_campaign",
        devices=original_dacs,
        environment_id="original_tec",
        operator="Original Operator",
        sensors={"temp": {"unit": "C"}},
    )
    original_config_id = output_data["config_id"]
    original_created_at = output_data["created_at"]

    # 2. Perform the update (including sensors to test Config API integration and datetime fields)
    start_time = datetime(2024, 2, 15, 10, 30, 0, tzinfo=timezone.utc)
    end_time = datetime(2024, 2, 15, 16, 45, 0, tzinfo=timezone.utc)

    update_data = {
        "campaign_id": "updated_campaign",
        "operator": "Updated Operator",
        "status": "in_progress",
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "sensors": {
            "temp": {"unit": "F"},
            "humidity": {"unit": "%"},
        },  # Updated sensors
    }
    response = client.put("/api/v1/tests/test_to_update", json=update_data)
    assert response.status_code == 200
    data = response.json()
    assert data["campaign_id"] == "updated_campaign"
    assert data["operator"] == "Updated Operator"
    assert len(data["devices"]) == 1
    assert data["devices"][0]["device_id"] == "original_dac"
    # device_version should be captured when status transitions to in_progress
    assert data["devices"][0]["device_version"] is not None
    assert data["created_at"] == original_created_at
    assert data["config_id"] == original_config_id
    assert data["status"] == "in_progress"

    # Verify the start and end fields are updated correctly
    expected_start = start_time.isoformat().replace("+00:00", "Z")
    expected_end = end_time.isoformat().replace("+00:00", "Z")
    assert data["start"] == expected_start
    assert data["end"] == expected_end
    assert UTC_ISO_DATATIME.match(data["updated_at"])

    # 3. Verify that the update was persisted
    response = client.get("/api/v1/tests/test_to_update")
    assert response.status_code == 200
    data = response.json()
    assert data["campaign_id"] == "updated_campaign"
    assert data["operator"] == "Updated Operator"
    assert data["devices"][0]["device_id"] == "original_dac"
    assert data["created_at"] == original_created_at
    assert data["config_id"] == original_config_id
    assert data["status"] == "in_progress"
    assert data["start"] == expected_start
    assert data["end"] == expected_end

    # 4. Verify the configuration was updated in Config API
    config_content = config_api.get(
        f"/api/v1/configurations/{original_config_id}/content"
    ).json()
    assert config_content["test_id"] == "test_to_update"
    assert config_content["campaign_id"] == "updated_campaign"
    assert len(config_content["devices"]) == 1
    assert config_content["devices"][0]["device_id"] == "original_dac"
    assert config_content["environment_id"] == "original_tec"
    assert config_content["operator"] == "Updated Operator"
    assert config_content["sensors"] == update_data["sensors"]
    assert config_content["sensors"]["temp"]["unit"] == "F"
    assert "humidity" in config_content["sensors"]


def test_update_test_not_found(client: TestClient) -> None:
    """
    Tests that a 404 is returned when trying to update a nonexistent test.
    """
    update_data = {"operator": "ghost"}
    response = client.put("/api/v1/tests/nonexistent_id", json=update_data)
    assert response.status_code == 404


def test_update_test_no_data(create_test: TestFactory, client: TestClient) -> None:
    """
    Tests that a 400 is returned if the update request has no data.
    """
    # Create a test first
    _, test_data = create_test(test_id="test_for_no_data_update")
    
    # Try to update with no data
    response = client.put("/api/v1/tests/test_for_no_data_update", json={})
    assert response.status_code == 400


def test_delete_test_not_found(client: TestClient) -> None:
    """
    Tests that a 404 is returned when trying to delete a nonexistent test.
    """
    response = client.delete("/api/v1/tests/nonexistent_id")
    assert response.status_code == 404


def test_delete_test(
    create_test: TestFactory,
    client: TestClient,
    config_api: httpx.Client,
    influx: InfluxDBClient,
    fs: Any,
) -> None:
    """
    Tests that a test can be successfully deleted.
    """
    # 1. Create a test to be deleted (with sensors to test Config API)
    test_id = "test_to_delete"
    _, created_test = create_test(test_id=test_id, sensors={"temp": {"unit": "C"}})
    config_id = created_test["config_id"]

    # Verify the configuration exists in Config API
    config_response = config_api.get(f"/api/v1/configurations/{config_id}")
    assert config_response.status_code == 200

    # 2. Upload some files for this test
    filenames = ["test_file1.txt", "test_file2.txt"]
    for filename in filenames:
        response = client.post(
            f"/api/v1/tests/{test_id}/files", json={"filename": filename}
        )
        assert response.status_code == 200
        url = urlparse(response.json()["url"])
        upload_path = f"{url.path}?{url.query}"
        response = client.post(upload_path, content=b"content")
        assert response.status_code == 200

    # Verify files exist in database
    response = client.get(f"/api/v1/tests/{test_id}/files")
    assert response.status_code == 200
    files = response.json()
    assert len(files) == 2
    assert set(file["name"] for file in files) == set(filenames)

    # Verify files exist in blob storage
    for filename in filenames:
        file_path = f"test-workspace/test-manager/{test_id}/{filename}"
        assert fs.exists(file_path), f"File {file_path} should exist in blob storage"

    # 3. Add some logbook entries for this test
    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "message 1", "operator": "Operator"},
    )
    assert response.status_code == 200
    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "message 2", "operator": "Operator"},
    )
    assert response.status_code == 200

    response = client.get(f"/api/v1/tests/{test_id}/logbook")
    assert response.status_code == 200
    logbook_entries = response.json()
    assert len(logbook_entries) == 2

    # Verify logbook entries exist in InfluxDB
    settings = get_settings().influx
    for entry in logbook_entries:
        result = influx.query(
            f'SELECT * FROM "{settings.measurement}" WHERE "id" = \'{entry["id"]}\''
        )
        assert len(list(result.get_points())) == 1

    # 4. Delete the test
    response = client.delete(f"/api/v1/tests/{test_id}")
    assert response.status_code == 204

    # 5. Verify it's gone
    response = client.get(f"/api/v1/tests/{test_id}")
    assert response.status_code == 404

    # 6. Verify files are also gone (should return 404 since test doesn't exist)
    response = client.get(f"/api/v1/tests/{test_id}/files")
    assert response.status_code == 404

    # Verify files are deleted from blob storage
    for filename in filenames:
        file_path = f"test-workspace/test-manager/{test_id}/{filename}"
        assert not fs.exists(file_path), (
            f"File {file_path} should be deleted from blob storage"
        )

    # 7. Verify the logbook entries are also gone from MongoDB
    response = client.get(f"/api/v1/tests/{test_id}/logbook")
    assert response.status_code == 200
    assert len(response.json()) == 0

    # 8. Verify logbook entries are also deleted from InfluxDB
    for entry in logbook_entries:
        result = influx.query(
            f'SELECT * FROM "{settings.measurement}" WHERE "id" = \'{entry["id"]}\''
        )
        assert len(list(result.get_points())) == 0

    # 9. Verify the configuration is deleted from Config API
    config_response = config_api.get(f"/api/v1/configurations/{config_id}")
    assert config_response.status_code == 404


def test_create_test_config_api_error(client: TestClient, create_dac: Any) -> None:
    """
    Tests that create_test returns HTTP 424 when config API fails.
    """
    # Create a Device first
    _, created_dac = create_dac(device_id="test_dac")

    # Mock the config API client to raise HTTPStatusError
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"

    mock_client = Mock()
    mock_client.post.return_value = mock_response
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server error", request=Mock(), response=mock_response
    )

    # Override the dependency
    client.app.dependency_overrides[get_config_api_client] = lambda: mock_client  # type: ignore

    try:
        test_data = {
            "test_id": "test_config_error",
            "campaign_id": "campaign1",
            "devices": [{"device_id": "test_dac", "device_version": None}],
            "environment_id": "tec1",
            "operator": "John Doe",
            "sensors": {"T1100": {"mp": "t_A_ODU_out_1", "unit": "°C"}},
        }

        response = client.post("/api/v1/tests", json=test_data)
        assert response.status_code == 424
        assert "Failed to create configuration" in response.json()["detail"]
        assert "500" in response.json()["detail"]
    finally:
        # Clean up the override
        client.app.dependency_overrides.clear()  # type: ignore


def test_update_test_config_api_error(client: TestClient, create_dac: Any) -> None:
    """
    Tests that update_test returns HTTP 424 when config API fails.
    """
    # Create a Device first
    _, created_dac = create_dac(device_id="test_dac_update")

    # First create a test successfully
    test_data = {
        "test_id": "test_update_error",
        "campaign_id": "campaign1",
        "devices": [{"device_id": "test_dac_update", "device_version": None}],
        "environment_id": "tec1",
        "operator": "John Doe",
        "sensors": {"T1100": {"mp": "t_A_ODU_out_1", "unit": "°C"}},
    }
    response = client.post("/api/v1/tests", json=test_data)
    assert response.status_code == 200

    # Mock the config API client to raise HTTPStatusError for POST requests
    mock_response = Mock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"

    mock_client = Mock()
    mock_client.post.return_value = mock_response
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Service unavailable", request=Mock(), response=mock_response
    )

    # Override the dependency
    client.app.dependency_overrides[get_config_api_client] = lambda: mock_client  # type: ignore

    try:
        update_data = {
            "sensors": {"T1101": {"mp": "t_A_ODU_out_2", "unit": "°C"}},
        }

        response = client.put("/api/v1/tests/test_update_error", json=update_data)
        assert response.status_code == 424
        assert "Failed to update configuration" in response.json()["detail"]
        assert "503" in response.json()["detail"]
    finally:
        # Clean up the override
        client.app.dependency_overrides.clear()  # type: ignore


def test_delete_test_config_api_error(client: TestClient, create_dac: Any) -> None:
    """
    Tests that delete_test returns HTTP 424 when config API fails.
    """
    # Create a Device first
    _, created_dac = create_dac(device_id="test_dac_delete")

    # First create a test successfully
    test_data = {
        "test_id": "test_delete_error",
        "campaign_id": "campaign1",
        "devices": [{"device_id": "test_dac_delete", "device_version": None}],
        "environment_id": "tec1",
        "operator": "John Doe",
        "sensors": {"T1100": {"mp": "t_A_ODU_out_1", "unit": "°C"}},
    }
    response = client.post("/api/v1/tests", json=test_data)
    assert response.status_code == 200

    # Mock the config API client to raise HTTPStatusError for DELETE requests
    mock_response = Mock()
    mock_response.status_code = 404
    mock_response.text = "Configuration not found"

    mock_client = Mock()
    mock_client.delete.return_value = mock_response
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not found", request=Mock(), response=mock_response
    )

    # Override the dependency
    client.app.dependency_overrides[get_config_api_client] = lambda: mock_client  # type: ignore

    try:
        response = client.delete("/api/v1/tests/test_delete_error")
        assert response.status_code == 424
        assert "Failed to delete configuration" in response.json()["detail"]
        assert "404" in response.json()["detail"]
    finally:
        # Clean up the override
        client.app.dependency_overrides.clear()  # type: ignore


def test_text_search_with_q_parameter(
    create_test: TestFactory, client: TestClient, create_dac: Any
) -> None:
    """Test that text search works with the q parameter."""
    # Create a Device for the test
    _, created_dac = create_dac(device_id="search-device")

    # Create a test with distinctive text
    create_test(
        test_id="text-search-test",
        campaign_id="searchable-campaign",
        devices=[{"device_id": "search-device", "device_version": None}],
        environment_id="production",
        operator="Alice Johnson",
    )

    # Test search in campaign_id - this should work with text index
    response = client.get("/api/v1/tests?q=searchable")
    assert response.status_code == 200
    results = response.json()
    assert len(results["items"]) >= 1, "Should find test with searchable text in campaign_id"
    found = any(test.get("test_id") == "text-search-test" for test in results["items"])
    assert found, "Should find our test when searching for 'searchable'"

    # Test search in operator field
    response = client.get("/api/v1/tests?q=Alice")
    assert response.status_code == 200
    results = response.json()
    assert len(results["items"]) >= 1, "Should find test with Alice in operator field"
    found = any(test.get("test_id") == "text-search-test" for test in results["items"])
    assert found, "Should find our test when searching for 'Alice'"


def test_get_test_full(create_test: TestFactory, client: TestClient) -> None:
    """
    Tests that the full endpoint returns test with all related data including logbook entries.
    """
    # Create a test
    test_id = "test-full-endpoint"
    create_test(test_id=test_id)

    # Create some logbook entries
    logbook_entry_1 = {
        "operator": "Alice",
        "content": "First logbook entry for full endpoint test.",
        "sensor_ids": ["sensor1", "sensor2"],
    }
    response = client.post(f"/api/v1/tests/{test_id}/logbook", json=logbook_entry_1)
    assert response.status_code == 200
    entry_1_id = response.json()["id"]

    logbook_entry_2 = {
        "operator": "Bob",
        "content": "Second logbook entry for full endpoint test.",
        "sensor_ids": ["sensor3"],
    }
    response = client.post(f"/api/v1/tests/{test_id}/logbook", json=logbook_entry_2)
    assert response.status_code == 200
    entry_2_id = response.json()["id"]

    # Get full test data
    response = client.get(f"/api/v1/tests/{test_id}/full")
    assert response.status_code == 200
    data = response.json()

    # Verify structure
    assert "test" in data
    assert "files" in data
    assert "logbook" in data
    assert "links" in data

    # Verify test data
    assert data["test"]["test_id"] == test_id

    # Verify logbook entries
    assert len(data["logbook"]) == 2
    logbook_ids = {entry["id"] for entry in data["logbook"]}
    assert entry_1_id in logbook_ids
    assert entry_2_id in logbook_ids

    # Verify logbook entry has correct fields (using 'id' not '_id')
    for entry in data["logbook"]:
        assert "id" in entry
        assert "_id" not in entry
        assert "test_id" in entry
        assert "operator" in entry
        assert "content" in entry
        assert "sensor_ids" in entry
        assert "timestamp" in entry
        assert "created_at" in entry

    # Verify files and links are present (empty arrays are fine)
    assert isinstance(data["files"], list)
    assert isinstance(data["links"], list)


# ============================================================================
# Pagination Tests
# ============================================================================


def test_pagination_default_parameters(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test that pagination uses default parameters (page=1, page_size=20)."""
    # Create a few tests
    for i in range(5):
        create_test(test_id=f"test-{i}")

    response = client.get("/api/v1/tests")
    assert response.status_code == 200
    data = response.json()

    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 5
    assert data["total_pages"] == 1
    assert len(data["items"]) == 5


def test_pagination_custom_page_size(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test pagination with custom page_size values (10, 20, 50, 100, 200)."""
    # Create 10 tests
    for i in range(10):
        create_test(test_id=f"test-{i}")

    # Test page_size=20
    response = client.get("/api/v1/tests?page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page_size"] == 20
    assert data["total"] == 10
    assert data["total_pages"] == 1
    assert len(data["items"]) == 10

    # Test page_size=100
    response = client.get("/api/v1/tests?page_size=100")
    assert response.status_code == 200
    data = response.json()
    assert data["page_size"] == 100
    assert data["total"] == 10
    assert len(data["items"]) == 10


def test_pagination_multiple_pages(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test pagination across multiple pages."""
    # Create 6 tests
    for i in range(6):
        create_test(test_id=f"test-{i:02d}")

    # Get page 1 with page_size=2
    response = client.get("/api/v1/tests?page=1&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 6
    assert data["total_pages"] == 1
    assert len(data["items"]) == 6

    # Create more tests to span multiple pages
    for i in range(6, 60):
        create_test(test_id=f"test-{i:02d}")

    # Now we have 60 tests total
    response = client.get("/api/v1/tests?page=1&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 20
    assert data["total"] == 60
    assert data["total_pages"] == 3
    assert len(data["items"]) == 20

    # Get page 2
    response = client.get("/api/v1/tests?page=2&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2
    assert data["total"] == 60
    assert data["total_pages"] == 3
    assert len(data["items"]) == 20

    # Get page 3 (last page)
    response = client.get("/api/v1/tests?page=3&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 3
    assert data["total"] == 60
    assert data["total_pages"] == 3
    assert len(data["items"]) == 20


def test_pagination_page_beyond_total(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test requesting a page beyond the total number of pages."""
    # Create 5 tests
    for i in range(5):
        create_test(test_id=f"test-{i}")

    # Request page 10 (beyond available data)
    response = client.get("/api/v1/tests?page=10&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 10
    assert data["total"] == 5
    assert data["total_pages"] == 1
    assert len(data["items"]) == 0  # Empty results


def test_pagination_with_filtering(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test pagination works correctly with filtering."""
    # Create tests with different operators
    for i in range(15):
        operator = "Alice" if i < 10 else "Bob"
        create_test(test_id=f"test-{i:02d}", operator=operator)

    # Filter by operator=Alice with page_size=20
    response = client.get("/api/v1/tests?operator=Alice&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 10
    assert data["total_pages"] == 1
    assert len(data["items"]) == 10
    assert all(item["operator"] == "Alice" for item in data["items"])

    # Filter by operator=Bob
    response = client.get("/api/v1/tests?operator=Bob&page_size=20")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    assert data["total_pages"] == 1
    assert len(data["items"]) == 5
    assert all(item["operator"] == "Bob" for item in data["items"])


def test_pagination_page_size_larger_than_total(
    create_test: TestFactory, client: TestClient
) -> None:
    """Test when page_size is larger than total number of items."""
    # Create 3 tests
    for i in range(3):
        create_test(test_id=f"test-{i}")

    # Request with page_size=100 (larger than total)
    response = client.get("/api/v1/tests?page_size=100")
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 100
    assert data["total"] == 3
    assert data["total_pages"] == 1
    assert len(data["items"]) == 3
