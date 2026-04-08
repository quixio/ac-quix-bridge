import time
from typing import Callable, ContextManager
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from quixportal import get_filesystem

from tests.conftest import TestFactory


def test_get_presigned_url_test_not_found(client: TestClient) -> None:
    response = client.post(
        "/api/v1/tests/nonexistent_id/files", json={"filename": "test.txt"}
    )
    assert response.status_code == 404


def test_upload_file_via_presigned_url(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_for_file_upload"
    create_test(test_id=test_id)

    # 1. Get presigned URL
    filename = "test.txt"
    response = client.post(
        f"/api/v1/tests/{test_id}/files", json={"filename": filename}
    )
    assert response.status_code == 200
    presigned_data = response.json()
    assert "url" in presigned_data
    assert presigned_data["url"].startswith("https://")

    # 2. Use the presigned URL to upload the file
    upload_url = presigned_data["url"]
    parsed_url = urlparse(upload_url)
    upload_path = f"{parsed_url.path}?{parsed_url.query}"

    file_content = b"This is a test file."
    response = client.post(upload_path, content=file_content)

    assert response.status_code == 200
    file_data = response.json()
    assert file_data["name"] == filename
    assert "id" in file_data
    assert file_data["size"] == len(file_content)
    assert file_data["url"].startswith("https://")
    # Check datetime format for generated timestamp
    assert file_data["uploaded_at"].endswith("Z")
    assert "T" in file_data["uploaded_at"]

    # 3. Verify the file was added to the test document in the db
    response = client.get(f"/api/v1/tests/{test_id}")
    assert response.status_code == 200
    test = response.json()
    assert len(test["files"]) == 1
    file_id = file_data["id"]
    assert file_id in test["files"]
    db_file_data = test["files"][file_id]
    assert db_file_data["name"] == filename
    assert db_file_data["size"] == len(file_content)
    # Check datetime format for generated timestamp
    assert db_file_data["uploaded_at"].endswith("Z")
    assert "T" in db_file_data["uploaded_at"]


def test_upload_file_invalid_signature(
    client: TestClient, create_test: TestFactory
) -> None:
    test_id = "test_invalid_sig"
    create_test(test_id=test_id)

    response = client.post(
        f"/api/v1/tests/{test_id}/files", json={"filename": "test.txt"}
    )
    assert response.status_code == 200
    presigned_data = response.json()
    upload_url = presigned_data["url"]

    # Tamper with the signature
    invalid_url = upload_url.replace("signature=", "signature=invalid")

    parsed_url = urlparse(invalid_url)
    upload_path = f"{parsed_url.path}?{parsed_url.query}"
    response = client.post(upload_path, content=b"content")
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid signature"


def test_upload_file_expired_signature(
    client: TestClient,
    create_test: TestFactory,
    override_settings: Callable[[int], ContextManager[None]],
) -> None:
    test_id = "test_expired_sig"
    create_test(test_id=test_id)

    # Get presigned URL with 1 second expiration
    with override_settings(1):
        response = client.post(
            f"/api/v1/tests/{test_id}/files", json={"filename": "test.txt"}
        )
    assert response.status_code == 200
    presigned_data = response.json()
    upload_url = presigned_data["url"]

    parsed_url = urlparse(upload_url)
    upload_path = f"{parsed_url.path}?{parsed_url.query}"

    # Wait for signature to expire
    time.sleep(1.1)

    # Now try to upload - should fail with expired signature
    response = client.post(upload_path, content=b"content")
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid signature"


def test_list_files_nonexistent_test(client: TestClient) -> None:
    response = client.get("/api/v1/tests/nonexistent_id/files")
    assert response.status_code == 404


def test_list_files_no_files(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_with_no_files"
    create_test(test_id=test_id)

    response = client.get("/api/v1/tests/test_with_no_files/files")
    assert response.status_code == 200
    assert response.json() == []


def test_list_files(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_for_listing_files"
    create_test(test_id=test_id)

    # Upload a file first
    filename = "test.txt"
    response = client.post(
        f"/api/v1/tests/{test_id}/files", json={"filename": filename}
    )
    assert response.status_code == 200
    presigned_data = response.json()
    upload_url = presigned_data["url"]
    parsed_url = urlparse(upload_url)
    upload_path = f"{parsed_url.path}?{parsed_url.query}"
    response = client.post(upload_path, content=b"content")
    assert response.status_code == 200
    uploaded_file = response.json()

    response = client.get(f"/api/v1/tests/{test_id}/files")
    assert response.status_code == 200
    file_list = response.json()
    assert len(file_list) == 1
    assert file_list[0]["id"] == uploaded_file["id"]
    assert file_list[0]["name"] == filename
    assert "url" in file_list[0]
    assert file_list[0]["size"] == len(b"content")
    assert "uploaded_at" in file_list[0]


def test_get_file(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_for_getting_file"
    create_test(test_id=test_id)

    # Upload a file first
    filename = "get_me.txt"
    response = client.post(
        f"/api/v1/tests/{test_id}/files", json={"filename": filename}
    )
    assert response.status_code == 200
    presigned_data = response.json()
    upload_url = presigned_data["url"]
    parsed_url = urlparse(upload_url)
    upload_path = f"{parsed_url.path}?{parsed_url.query}"
    file_content = b"content"
    response = client.post(upload_path, content=file_content)
    assert response.status_code == 200
    uploaded_file = response.json()
    file_id = uploaded_file["id"]

    # Get the file
    response = client.get(f"/api/v1/tests/{test_id}/files/{file_id}")
    assert response.status_code == 200
    file_data = response.json()
    assert file_data["id"] == file_id
    assert file_data["name"] == "get_me.txt"
    assert "url" in file_data
    assert file_data["size"] == len(file_content)
    assert "uploaded_at" in file_data

    # Verify that getting a non-existent file returns 404
    response = client.get(f"/api/v1/tests/{test_id}/files/nonexistent")
    assert response.status_code == 404


def test_download_file(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_for_downloading_file"
    create_test(test_id=test_id)

    # Upload a file first
    file_content = b"This is a test file to download."
    file_name = "download_me.txt"
    response = client.post(
        f"/api/v1/tests/{test_id}/files", json={"filename": file_name}
    )
    assert response.status_code == 200
    presigned_data = response.json()
    upload_url = presigned_data["url"]
    parsed_url = urlparse(upload_url)
    upload_path = f"{parsed_url.path}?{parsed_url.query}"
    response = client.post(upload_path, content=file_content)
    assert response.status_code == 200
    uploaded_file = response.json()
    file_id = uploaded_file["id"]

    # Download the file
    response = client.get(f"/api/v1/tests/{test_id}/files/{file_id}/download")
    assert response.status_code == 200
    assert response.content == file_content
    assert (
        f"attachment; filename={file_name}" in response.headers["content-disposition"]
    )


def test_delete_file(client: TestClient, create_test: TestFactory) -> None:
    test_id = "test_for_deleting_files"
    create_test(test_id=test_id)

    # Upload a file first
    filename = "delete_me.txt"
    file_content = b"content to be deleted"
    response = client.post(
        f"/api/v1/tests/{test_id}/files", json={"filename": filename}
    )
    assert response.status_code == 200
    presigned_data = response.json()
    upload_url = presigned_data["url"]
    parsed_url = urlparse(upload_url)
    upload_path = f"{parsed_url.path}?{parsed_url.query}"
    response = client.post(upload_path, content=file_content)
    assert response.status_code == 200
    uploaded_file = response.json()
    file_id = uploaded_file["id"]

    # Verify file exists in filesystem
    fs = get_filesystem()
    file_path = f"test-workspace/test-manager/{test_id}/{filename}"
    assert fs.exists(file_path), f"File {file_path} should exist in filesystem"

    # Delete the file
    response = client.delete(f"/api/v1/tests/{test_id}/files/{file_id}")
    assert response.status_code == 204

    # Verify it's gone from database
    response = client.get(f"/api/v1/tests/{test_id}/files")
    assert response.status_code == 200
    assert response.json() == []

    # Verify file is actually deleted from filesystem
    assert not fs.exists(file_path), f"File {file_path} should not exist in filesystem"

    # Verify that deleting again returns 404
    response = client.delete(f"/api/v1/tests/{test_id}/files/{file_id}")
    assert response.status_code == 404
