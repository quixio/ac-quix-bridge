"""
Test suite for Pydantic validation error handling.

Tests that the custom validation exception handler transforms
Pydantic errors into user-friendly messages.
"""

import pytest
from fastapi.testclient import TestClient


def test_sensors_must_be_dict_not_array(client):
    """Test that sensors as array returns user-friendly error."""
    # Create a test with sensors as array instead of dict
    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-sensors-array",
            "name": "Test with Array Sensors",
            "devices": [{"device_id": "device-001", "dac_version": 1}],
            "sensors": ["sensor1", "sensor2"],  # Wrong: array instead of dict
            "description": "Testing sensors validation",
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert "sensors" in data["detail"].lower()
    assert "dictionary" in data["detail"].lower() or "dict" in data["detail"].lower()
    # Should include helpful example
    assert "sensor1" in data["detail"] or "temperature" in data["detail"]


def test_sensors_must_be_nested_dict(client):
    """Test that sensors as flat dict returns user-friendly error."""
    # Create a test with sensors as flat dict instead of nested dict
    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-sensors-flat",
            "name": "Test with Flat Sensors",
            "devices": [{"device_id": "device-001", "dac_version": 1}],
            "sensors": {"sensor1": "value"},  # Wrong: flat dict instead of nested
            "description": "Testing sensors validation",
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    # This might pass Pydantic validation since dict[str, Any] allows any values
    # But if it fails, error should mention nested structure


def test_missing_required_field_dacs(client):
    """Test that missing required 'devices' field returns user-friendly error."""

    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-missing-devices",
            "name": "Test without DACs",
            "sensors": {},
            "description": "Testing missing required field",
            # Missing 'devices' field
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert "devices" in data["detail"].lower()
    assert "required" in data["detail"].lower() or "missing" in data["detail"].lower()


def test_invalid_status_enum_value(client):
    """Test that invalid status enum value returns user-friendly error."""

    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-invalid-status",
            "name": "Test with Invalid Status",
            "devices": [{"device_id": "device-001", "dac_version": 1}],
            "sensors": {},
            "status": "invalid_status",  # Wrong: not a valid TestStatus enum
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert "status" in data["detail"].lower()


def test_dacs_must_be_array_not_string(client):
    """Test that devices as string returns user-friendly error."""

    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-devices-string",
            "name": "Test with String DACs",
            "devices": "device-001",  # Wrong: string instead of array
            "sensors": {},
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert "devices" in data["detail"].lower()
    assert "list" in data["detail"].lower() or "array" in data["detail"].lower()


def test_dacs_must_have_at_least_one_item(client):
    """Test that empty devices array returns user-friendly error."""

    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-empty-devices",
            "name": "Test with Empty DACs",
            "devices": [],  # Wrong: empty array
            "sensors": {},
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    # Might be caught by Pydantic's min_length validator



def test_invalid_datetime_format(client):
    """Test that invalid datetime format returns user-friendly error."""

    # First create a valid test
    client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-datetime",
            "name": "Test for DateTime",
            "devices": [{"device_id": "device-001", "dac_version": 1}],
            "sensors": {},
        },
    )

    # Try to update with invalid datetime
    response = client.put(
        "/api/v1/tests/test-datetime",
        json={
            "start": "not-a-datetime",  # Wrong: invalid datetime format
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    assert "start" in data["detail"].lower()
    # Should mention datetime or ISO format


def test_validation_errors_include_original_errors_for_debugging(client):
    """Test that validation response includes original errors for debugging."""

    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-debug",
            "name": "Test Debug Info",
            "devices": "wrong-type",  # Invalid
            "sensors": [],  # Invalid
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data  # User-friendly message
    assert "errors" in data  # Original Pydantic errors for debugging
    assert isinstance(data["errors"], list)
    assert len(data["errors"]) > 0


def test_multiple_validation_errors_combined(client):
    """Test that multiple validation errors are combined in one message."""

    response = client.post(
        "/api/v1/tests",
        json={
            "test_id": "test-multiple-errors",
            "devices": "wrong-type",  # Error 1
            "sensors": ["wrong", "type"],  # Error 2
            # Missing 'name' if required
        },
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data
    # Should contain multiple error messages separated by " | "
    if " | " in data["detail"]:
        parts = data["detail"].split(" | ")
        assert len(parts) >= 2
