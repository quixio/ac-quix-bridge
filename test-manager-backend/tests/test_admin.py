"""Tests for admin endpoints (seed demo data)."""

import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database


def test_seed_demo_data_default_params(client: TestClient, mongo: None) -> None:
    """Test seeding demo data with default parameters."""
    response = client.post("/api/v1/admin/seed-demo-data")
    assert response.status_code == 200

    data = response.json()
    assert data["devices_created"] == 10
    assert data["tests_created"] == 10
    assert data["journal_entries_created"] > 0
    assert data["logbook_entries_created"] > 0
    assert "message" in data


def test_seed_demo_data_custom_quantities(client: TestClient, mongo: None) -> None:
    """Test seeding demo data with custom quantities."""
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={
            "num_devices": 5,
            "num_tests": 3,
            "include_journals": True,
            "include_logbook": True,
        }
    )
    assert response.status_code == 200

    data = response.json()
    assert data["devices_created"] == 5
    assert data["tests_created"] == 3
    assert data["journal_entries_created"] > 0
    assert data["logbook_entries_created"] > 0


def test_seed_demo_data_without_journals(client: TestClient, mongo: None) -> None:
    """Test seeding demo data without journal entries."""
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={
            "num_devices": 3,
            "num_tests": 2,
            "include_journals": False,
            "include_logbook": True,
        }
    )
    assert response.status_code == 200

    data = response.json()
    assert data["devices_created"] == 3
    assert data["tests_created"] == 2
    assert data["journal_entries_created"] == 0  # No journals created
    assert data["logbook_entries_created"] > 0


def test_seed_demo_data_without_logbook(client: TestClient, mongo: None) -> None:
    """Test seeding demo data without logbook entries."""
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={
            "num_devices": 3,
            "num_tests": 2,
            "include_journals": True,
            "include_logbook": False,
        }
    )
    assert response.status_code == 200

    data = response.json()
    assert data["devices_created"] == 3
    assert data["tests_created"] == 2
    assert data["journal_entries_created"] > 0
    assert data["logbook_entries_created"] == 0  # No logbook entries created


def test_seed_demo_data_minimal(client: TestClient, mongo: None) -> None:
    """Test seeding demo data with minimal configuration."""
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={
            "num_devices": 1,
            "num_tests": 1,
            "include_journals": False,
            "include_logbook": False,
        }
    )
    assert response.status_code == 200

    data = response.json()
    assert data["devices_created"] == 1
    assert data["tests_created"] == 1
    assert data["journal_entries_created"] == 0
    assert data["logbook_entries_created"] == 0


def test_seed_demo_data_large_quantities(client: TestClient, mongo: None) -> None:
    """Test seeding demo data with maximum allowed quantities."""
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={
            "num_devices": 20,
            "num_tests": 15,
            "include_journals": True,
            "include_logbook": True,
        }
    )
    assert response.status_code == 200

    data = response.json()
    assert data["devices_created"] == 20
    assert data["tests_created"] == 15
    # Each Device gets ~3-4 journal entries
    assert data["journal_entries_created"] >= 60
    # Each test gets 2-5 logbook entries
    assert data["logbook_entries_created"] >= 30


def test_seed_demo_data_creates_unique_dacs(client: TestClient, mongo: None) -> None:
    """Test that seeded DACs have unique IDs."""
    # Seed 5 DACs
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={"num_devices": 5, "num_tests": 1}
    )
    assert response.status_code == 200

    # Get all DACs
    response = client.get("/api/v1/devices")
    assert response.status_code == 200
    data = response.json()
    devices = data["items"]
    assert len(devices) == 5

    # Verify all Device IDs are unique
    device_ids = [device["device_id"] for device in devices]
    assert len(device_ids) == len(set(device_ids))


def test_seed_demo_data_creates_unique_tests(client: TestClient, mongo: None) -> None:
    """Test that seeded Tests have unique IDs."""
    # Seed 5 Tests
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={"num_devices": 2, "num_tests": 5}
    )
    assert response.status_code == 200

    # Get all Tests
    response = client.get("/api/v1/tests")
    assert response.status_code == 200
    tests_data = response.json()
    tests = tests_data["items"]
    assert len(tests) == 5

    # Verify all Test IDs are unique
    test_ids = [test["test_id"] for test in tests]
    assert len(test_ids) == len(set(test_ids))


def test_seed_demo_data_tests_reference_dacs(client: TestClient, mongo: None) -> None:
    """Test that seeded Tests correctly reference created DACs."""
    # Seed data
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={"num_devices": 5, "num_tests": 3}
    )
    assert response.status_code == 200

    # Get all DACs and Tests
    dacs_response = client.get("/api/v1/devices")
    tests_response = client.get("/api/v1/tests")

    dacs_data = dacs_response.json()
    tests_data = tests_response.json()
    devices = dacs_data["items"]
    tests = tests_data["items"]

    device_ids = {device["device_id"] for device in devices}

    # Verify each test references valid DACs
    for test in tests:
        assert "devices" in test
        assert len(test["devices"]) >= 1  # At least one Device
        assert len(test["devices"]) <= 3  # At most 3 DACs (as per implementation)

        # All referenced DACs should exist
        for dac_ref in test["devices"]:
            assert dac_ref["device_id"] in device_ids
            assert "device_version" in dac_ref


def test_seed_demo_data_journal_entries_created(client: TestClient, mongo: None) -> None:
    """Test that journal entries are created for DACs."""
    # Seed with journals enabled
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={"num_devices": 3, "num_tests": 1, "include_journals": True}
    )
    assert response.status_code == 200
    data = response.json()

    # Get all DACs
    dacs_response = client.get("/api/v1/devices")
    dacs_data = dacs_response.json()
    devices = dacs_data["items"]

    # Get journal entries for each Device
    total_journal_entries = 0
    for device in devices:
        journal_response = client.get(f"/api/v1/devices/{device['device_id']}/journal")
        assert journal_response.status_code == 200
        journal = journal_response.json()

        # Each Device should have journal entries (3-4 per Device based on implementation)
        assert len(journal) >= 3
        total_journal_entries += len(journal)

    # Verify the count matches what was reported
    assert total_journal_entries == data["journal_entries_created"]


def test_seed_demo_data_logbook_counts(client: TestClient, mongo: None) -> None:
    """Test that logbook entry counts are within expected range."""
    # Seed with logbook enabled
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={"num_devices": 5, "num_tests": 5, "include_logbook": True, "include_journals": False}
    )
    assert response.status_code == 200
    data = response.json()

    # Verify the API returned expected counts
    assert data["devices_created"] == 5
    assert data["tests_created"] == 5
    assert data["journal_entries_created"] == 0  # journals disabled
    # Each test gets 2-5 logbook entries, so with 5 tests: 10-25 entries
    assert data["logbook_entries_created"] >= 10
    assert data["logbook_entries_created"] <= 25


def test_seed_demo_data_lookup_tables_exist(client: TestClient, mongo: None) -> None:
    """Test that seed operation seeds lookup tables if they don't exist."""
    # Seed data - this will also seed lookup tables
    response = client.post(
        "/api/v1/admin/seed-demo-data",
        params={"num_devices": 1, "num_tests": 1}
    )
    assert response.status_code == 200

    # Verify lookup tables were seeded
    sample_types_response = client.get("/api/v1/lookups/sample-types")
    locations_response = client.get("/api/v1/lookups/locations")

    assert sample_types_response.status_code == 200
    assert locations_response.status_code == 200

    sample_types = sample_types_response.json()
    locations = locations_response.json()

    # Should have data from CSV seeding
    assert len(sample_types) > 0
    assert len(locations) > 0
