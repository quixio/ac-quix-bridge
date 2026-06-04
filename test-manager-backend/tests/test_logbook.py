import time

from fastapi.testclient import TestClient

from tests.conftest import TestFactory


def _create_test_with_session(
    client: TestClient, create_test: TestFactory
) -> tuple[str, str]:
    """Create a test and attach one session to it. Returns (test_id, session_id)."""
    _, created = create_test()
    test_id = created["test_id"]
    session_id = "2026-05-22T10:30:00"
    response = client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={
            "session_id": session_id,
            "track": "ks_nurburgring",
            "car_model": "bmw_1m",
        },
    )
    assert response.status_code == 200
    return test_id, session_id


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


def test_create_logbook_entry_with_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """Logbook entry can be scoped to a specific session via session_id."""
    _, created = create_test()
    test_id = created["test_id"]

    session_id = "2026-05-22T10:30:00"
    add_session = client.post(
        f"/api/v1/tests/{test_id}/sessions",
        json={
            "session_id": session_id,
            "track": "ks_nurburgring",
            "car_model": "bmw_1m",
        },
    )
    assert add_session.status_code == 200

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "Tyre pressures off mid-stint", "session_id": session_id},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == session_id
    assert body["content"] == "Tyre pressures off mid-stint"


def test_create_logbook_entry_without_session_id_is_test_wide(
    client: TestClient, create_test: TestFactory
) -> None:
    """Omitting session_id yields a test-wide entry (session_id is None)."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "Pre-test prep notes"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] is None
    assert body["content"] == "Pre-test prep notes"


def test_create_logbook_entry_rejects_unknown_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """Posting with a session_id not on the test returns 400."""
    _, created = create_test()
    test_id = created["test_id"]

    response = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "Note", "session_id": "2099-01-01T00:00:00.000Z"},
    )
    assert response.status_code == 400
    assert "session_id" in response.json()["detail"].lower()


# ============================================================================
# List
# ============================================================================


def test_get_logbook_entries_empty(
    client: TestClient, create_test: TestFactory
) -> None:
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


def test_list_logbook_filters_by_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """?session_id=... returns only entries scoped to that session."""
    test_id, session_id = _create_test_with_session(client, create_test)

    client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "tied", "session_id": session_id},
    )
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "wide"})

    response = client.get(f"/api/v1/tests/{test_id}/logbook?session_id={session_id}")
    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == 1
    assert entries[0]["content"] == "tied"


def test_list_logbook_include_test_wide(
    client: TestClient, create_test: TestFactory
) -> None:
    """?session_id=...&include_test_wide=true returns scoped + test-wide entries."""
    test_id, session_id = _create_test_with_session(client, create_test)

    client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "tied", "session_id": session_id},
    )
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "wide"})

    response = client.get(
        f"/api/v1/tests/{test_id}/logbook?session_id={session_id}&include_test_wide=true"
    )
    assert response.status_code == 200
    contents = {e["content"] for e in response.json()}
    assert contents == {"tied", "wide"}


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


def test_get_logbook_entry_not_found(
    client: TestClient, create_test: TestFactory
) -> None:
    """Test that getting a non-existent entry returns 404."""
    _, created = create_test()
    response = client.get(f"/api/v1/tests/{created['test_id']}/logbook/nonexistent")
    assert response.status_code == 404


def test_get_logbook_entry_wrong_test_id(
    client: TestClient, create_test: TestFactory
) -> None:
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


def test_update_logbook_entry_attach_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """A test-wide entry can be reattached to a known session via PUT."""
    test_id, session_id = _create_test_with_session(client, create_test)

    created = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "later attached"},
    ).json()
    entry_id = created["id"]

    response = client.put(
        f"/api/v1/tests/{test_id}/logbook/{entry_id}",
        json={"session_id": session_id},
    )
    assert response.status_code == 200
    assert response.json()["session_id"] == session_id


def test_update_logbook_entry_clear_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """Explicit `{session_id: null}` PUT clears the session attachment."""
    test_id, session_id = _create_test_with_session(client, create_test)

    created = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "scoped", "session_id": session_id},
    ).json()
    entry_id = created["id"]

    response = client.put(
        f"/api/v1/tests/{test_id}/logbook/{entry_id}",
        json={"session_id": None},
    )
    assert response.status_code == 200
    assert response.json()["session_id"] is None


def test_update_logbook_entry_rejects_unknown_session_id(
    client: TestClient, create_test: TestFactory
) -> None:
    """PUT with an unknown non-null session_id returns 400."""
    _, created = create_test()
    test_id = created["test_id"]

    entry = client.post(
        f"/api/v1/tests/{test_id}/logbook",
        json={"content": "wide"},
    ).json()
    entry_id = entry["id"]

    response = client.put(
        f"/api/v1/tests/{test_id}/logbook/{entry_id}",
        json={"session_id": "2099-01-01T00:00:00.000Z"},
    )
    assert response.status_code == 400
    assert "session_id" in response.json()["detail"].lower()


# ============================================================================
# Delete
# ============================================================================


def test_get_test_full_data_returns_logbook_sorted_by_created_at_desc(
    client: TestClient, create_test: TestFactory
) -> None:
    """Regression for the `.sort('timestamp', -1)` drift bug in tests.py:288."""
    _, created = create_test()
    test_id = created["test_id"]

    # now() resolves to whole seconds — sleep > 1s between writes so created_at
    # actually differs for sort purposes.
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "first"})
    time.sleep(1.1)
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "second"})
    time.sleep(1.1)
    client.post(f"/api/v1/tests/{test_id}/logbook", json={"content": "third"})

    response = client.get(f"/api/v1/tests/{test_id}/full")
    assert response.status_code == 200
    logbook = response.json()["logbook"]
    assert [e["content"] for e in logbook] == ["third", "second", "first"]


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
