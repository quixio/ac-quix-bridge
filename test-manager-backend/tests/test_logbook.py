from fastapi.testclient import TestClient

from tests.conftest import TestFactory


# ============================================================================
# Create
# ============================================================================


def test_create_logbook_entry_test_not_found(client: TestClient) -> None:
    """Test that creating a logbook entry for non-existent test returns 404."""
    response = client.post(
        "/api/v1/tests/nonexistent/logbook",
        json={"content": "Some note."},
    )
    assert response.status_code == 404


def test_create_logbook_entry(client: TestClient, create_test: TestFactory) -> None:
    """Test creating a logbook entry."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "This is a logbook entry."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["test_id"] == test_id
    assert data["content"] == "This is a logbook entry."
    assert "id" in data
    assert "created_at" in data


# ============================================================================
# List
# ============================================================================


def test_get_logbook_entries_empty(client: TestClient, create_test: TestFactory) -> None:
    """Test that empty list is returned when no entries exist."""
    _, created = create_test()
    response = client.get(f"/api/v1/tests/{created['test_id']}/logbook")
    assert response.status_code == 200
    assert response.json() == []


def test_get_logbook_entries(client: TestClient, create_test: TestFactory) -> None:
    """Test listing logbook entries."""
    _, created = create_test()
    test_id = created["test_id"]

    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "First entry."})
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "Second entry."})

    response = client.get(f"/api/v1/tests/{test_id}/logbook")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2


# ============================================================================
# Get single
# ============================================================================


def test_get_logbook_entry(client: TestClient, create_test: TestFactory) -> None:
    """Test retrieving a single logbook entry."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "Single entry."},
    )
    entry_id = response.json()["id"]

    response = client.get(f"/api/v1/tests/{test_id}/logbook/{entry_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == entry_id
    assert data["test_id"] == test_id
    assert data["content"] == "Single entry."


def test_get_logbook_entry_not_found(client: TestClient, create_test: TestFactory) -> None:
    """Test that getting a non-existent entry returns 404."""
    _, created = create_test()
    response = client.get(f"/api/v1/tests/{created['test_id']}/logbook/nonexistent")
    assert response.status_code == 404


def test_get_logbook_entry_wrong_test_id(client: TestClient, create_test: TestFactory) -> None:
    """Test that getting an entry with wrong test_id returns 404."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "Entry."},
    )
    entry_id = response.json()["id"]

    response = client.get(f"/api/v1/tests/wrong-test-id/logbook/{entry_id}")
    assert response.status_code == 404


# ============================================================================
# Update
# ============================================================================


def test_update_logbook_entry(client: TestClient, create_test: TestFactory) -> None:
    """Test updating a logbook entry's content."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "Initial content."},
    )
    entry_id = response.json()["id"]

    response = client.put(
        f"/api/v1/tests/{test_id}/logbook/{entry_id}",
        json={"content": "Updated content."},
    )
    assert response.status_code == 200
    assert response.json()["content"] == "Updated content."


def test_update_logbook_entry_not_found(client: TestClient) -> None:
    """Test that updating a non-existent entry returns 404."""
    response = client.put(
        "/api/v1/tests/some_test/logbook/nonexistent",
        json={"content": "ghost"},
    )
    assert response.status_code == 404


def test_update_logbook_entry_no_data(client: TestClient) -> None:
    """Test that updating with no fields returns 400."""
    response = client.put("/api/v1/tests/some_test/logbook/some_id", json={})
    assert response.status_code == 400


# ============================================================================
# Delete
# ============================================================================


def test_delete_logbook_entry(client: TestClient, create_test: TestFactory) -> None:
    """Test deleting a logbook entry."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "To be deleted."},
    )
    entry_id = response.json()["id"]

    # Wrong test_id
    response = client.delete(f"/api/v1/tests/nonexistent/logbook/{entry_id}")
    assert response.status_code == 404

    # Wrong entry_id
    response = client.delete(f"/api/v1/tests/{test_id}/logbook/nonexistent")
    assert response.status_code == 404

    # Correct delete
    response = client.delete(f"/api/v1/tests/{test_id}/logbook/{entry_id}")
    assert response.status_code == 204

    # Verify gone
    response = client.get(f"/api/v1/tests/{test_id}/logbook/{entry_id}")
    assert response.status_code == 404
