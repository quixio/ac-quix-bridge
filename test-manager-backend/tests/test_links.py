from fastapi.testclient import TestClient

from tests.conftest import TestFactory


def test_add_link_test_not_found(client: TestClient) -> None:
    link_data = {"url": "http://example.com", "label": "Example"}
    response = client.post(
        "/api/v1/tests/nonexistent_id/links",
        json=link_data,
    )
    assert response.status_code == 404


def test_add_link(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_for_links"
    create_test(test_id=test_id)

    link_data = {"url": "http://example.com", "label": "Example"}
    response = client.post(
        "/api/v1/tests/test_for_links/links",
        json=link_data,
    )
    assert response.status_code == 200
    link = response.json()
    assert link["url"] == "http://example.com"
    assert link["label"] == "Example"
    assert "id" in link

    # Verify that the link was added to the test document in the db
    response = client.get("/api/v1/tests/test_for_links")
    assert response.status_code == 200
    test = response.json()
    assert len(test["links"]) == 1
    assert test["links"][0]["id"] == link["id"]
    assert test["links"][0]["url"] == "http://example.com"
    assert test["links"][0]["label"] == "Example"


def test_list_links(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_for_listing_links"
    create_test(test_id=test_id)

    # Test with no links
    response = client.get("/api/v1/tests/test_for_listing_links/links")
    assert response.status_code == 200
    assert response.json() == []

    # Add a link
    link_data = {"url": "http://a.com", "label": "A"}
    response = client.post(
        "/api/v1/tests/test_for_listing_links/links",
        json=link_data,
    )
    assert response.status_code == 200
    link_a = response.json()

    # Add another link
    link_data = {"url": "http://b.com", "label": "B"}
    response = client.post(
        "/api/v1/tests/test_for_listing_links/links",
        json=link_data,
    )
    assert response.status_code == 200
    link_b = response.json()

    # List links
    response = client.get("/api/v1/tests/test_for_listing_links/links")
    assert response.status_code == 200
    links = response.json()
    assert len(links) == 2
    assert link_a in links
    assert link_b in links


def test_delete_link(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_for_deleting_links"
    create_test(test_id=test_id)

    # Add a link
    link_data = {"url": "http://delete.me", "label": "Delete Me"}
    response = client.post(
        "/api/v1/tests/test_for_deleting_links/links",
        json=link_data,
    )
    assert response.status_code == 200
    link_to_delete = response.json()
    link_id = link_to_delete["id"]

    # Test deleting from non-existent test
    response = client.delete(f"/api/v1/tests/nonexistent/links/{link_id}")
    assert response.status_code == 404

    # Test deleting non-existent link
    response = client.delete("/api/v1/tests/test_for_deleting_links/links/nonexistent")
    assert response.status_code == 404

    # Delete the link
    response = client.delete(f"/api/v1/tests/test_for_deleting_links/links/{link_id}")
    assert response.status_code == 204

    # Verify it's gone
    response = client.get("/api/v1/tests/test_for_deleting_links/links")
    assert response.status_code == 200
    assert response.json() == []
