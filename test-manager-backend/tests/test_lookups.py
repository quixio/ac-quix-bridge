from fastapi.testclient import TestClient
from pymongo.database import Database

from api.mongo import get_mongo


def test_list_sample_types_auto_seeded(client: TestClient) -> None:
    """Test that sample types are automatically seeded from CSV."""
    response = client.get("/api/v1/lookups/sample-types")
    assert response.status_code == 200

    data = response.json()
    # Auto-seeding should populate sample types from CSV
    assert len(data) > 0

    # Verify structure
    assert all("id" in st for st in data)
    assert all("sample_type" in st for st in data)

    # Check for expected sample types from seed data
    sample_type_ids = [st["id"] for st in data]
    assert "PFP" in sample_type_ids
    assert "FP" in sample_type_ids


def test_list_sample_types_with_additional_data(client: TestClient) -> None:
    """Test that we can add more sample types beyond the auto-seeded ones."""
    # Get mongo and insert additional test data
    app = client.app
    mongo = app.dependency_overrides.get(get_mongo, get_mongo)()

    # Insert additional sample types
    mongo.sample_types.insert_many([
        {"_id": "TEST1", "sample_type": "Test Sample 1"},
        {"_id": "TEST2", "sample_type": "Test Sample 2"},
    ])

    response = client.get("/api/v1/lookups/sample-types")
    assert response.status_code == 200

    data = response.json()
    # Should have auto-seeded + our 2 test samples
    assert len(data) > 2

    sample_type_ids = [st["id"] for st in data]
    assert "TEST1" in sample_type_ids
    assert "TEST2" in sample_type_ids


def test_list_locations_auto_seeded(client: TestClient) -> None:
    """Test that locations are automatically seeded from CSV."""
    response = client.get("/api/v1/lookups/locations")
    assert response.status_code == 200

    data = response.json()
    # Auto-seeding should populate locations from CSV
    assert len(data) > 0

    # Verify structure
    assert all("id" in loc for loc in data)
    assert all("location" in loc for loc in data)

    # Check for some expected locations from seed data
    location_ids = [loc["id"] for loc in data]
    # These are locations from the generic CSV file
    assert "Lab-A-01" in location_ids or "TestBench-01" in location_ids  # Should have generic locations
    assert len(location_ids) > 10  # Should have a reasonable number of locations


def test_list_locations_with_additional_data(client: TestClient) -> None:
    """Test that we can add more locations beyond the auto-seeded ones."""
    # Get mongo and insert additional test data
    app = client.app
    mongo = app.dependency_overrides.get(get_mongo, get_mongo)()

    # Insert additional locations
    mongo.locations.insert_many([
        {"_id": "TEST_BENCH_1", "location": "Test Bench 1"},
        {"_id": "TEST_BENCH_2", "location": "Test Bench 2"},
    ])

    response = client.get("/api/v1/lookups/locations")
    assert response.status_code == 200

    data = response.json()
    # Should have auto-seeded + our 2 test locations
    assert len(data) > 2

    location_ids = [loc["id"] for loc in data]
    assert "TEST_BENCH_1" in location_ids
    assert "TEST_BENCH_2" in location_ids
