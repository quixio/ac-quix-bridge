import re

from fastapi.testclient import TestClient

from tests.conftest import ExperimentFactory

UTC_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")


# ============================================================================
# Create
# ============================================================================


def test_create_experiment(client: TestClient) -> None:
    """Create an experiment with an auto-generated ID."""
    response = client.post("/api/v1/experiments", json={"name": "tyre_pressure"})
    assert response.status_code == 200
    data = response.json()
    assert data["experiment_id"] == "EXP-0001"
    assert data["name"] == "tyre_pressure"
    assert UTC_ISO_DATETIME.match(data["created_at"])


def test_create_experiment_auto_increment(client: TestClient) -> None:
    client.post("/api/v1/experiments", json={"name": "exp_a"})
    response = client.post("/api/v1/experiments", json={"name": "exp_b"})
    assert response.json()["experiment_id"] == "EXP-0002"


def test_create_experiment_duplicate_name_409(
    create_experiment: ExperimentFactory, client: TestClient
) -> None:
    """A duplicate name collides on the same lake partition — reject it."""
    create_experiment(name="tyre_pressure")
    response = client.post("/api/v1/experiments", json={"name": "tyre_pressure"})
    assert response.status_code == 409


def test_create_experiment_case_sensitive_names_distinct(client: TestClient) -> None:
    """Case differs the lake partition, so names are case-sensitive (both allowed)."""
    assert (
        client.post("/api/v1/experiments", json={"name": "TestDrive"}).status_code
        == 200
    )
    assert (
        client.post("/api/v1/experiments", json={"name": "testdrive"}).status_code
        == 200
    )


# ============================================================================
# Name validation (partition-safe)
# ============================================================================


def test_create_experiment_allows_spaces_case_hyphens(client: TestClient) -> None:
    """Spaces/case/hyphens are proven safe in the lake — accept them verbatim."""
    response = client.post(
        "/api/v1/experiments", json={"name": "Brake Balance-Sweep 2"}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Brake Balance-Sweep 2"


def test_create_experiment_trims_name(client: TestClient) -> None:
    response = client.post("/api/v1/experiments", json={"name": "  spaced  "})
    assert response.status_code == 200
    assert response.json()["name"] == "spaced"


def test_create_experiment_rejects_slash(client: TestClient) -> None:
    """A slash breaks the Hive partition path."""
    response = client.post("/api/v1/experiments", json={"name": "a/b"})
    assert response.status_code == 422


def test_create_experiment_rejects_backslash(client: TestClient) -> None:
    response = client.post("/api/v1/experiments", json={"name": "a\\b"})
    assert response.status_code == 422


def test_create_experiment_rejects_blank(client: TestClient) -> None:
    response = client.post("/api/v1/experiments", json={"name": "   "})
    assert response.status_code == 422


def test_create_experiment_rejects_control_char(client: TestClient) -> None:
    response = client.post("/api/v1/experiments", json={"name": "a\nb"})
    assert response.status_code == 422


# ============================================================================
# List
# ============================================================================


def test_list_experiments_empty(client: TestClient) -> None:
    response = client.get("/api/v1/experiments")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_list_experiments_with_data(
    create_experiment: ExperimentFactory, client: TestClient
) -> None:
    create_experiment(name="tyre_pressure")
    create_experiment(name="brake_balance")

    response = client.get("/api/v1/experiments")
    assert response.json()["total"] == 2


def test_list_experiments_text_search(
    create_experiment: ExperimentFactory, client: TestClient
) -> None:
    create_experiment(name="tyre_pressure")
    create_experiment(name="brake_balance")

    response = client.get("/api/v1/experiments?q=brake")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "brake_balance"


def test_list_experiments_name_filter_case_insensitive(
    create_experiment: ExperimentFactory, client: TestClient
) -> None:
    """The ?name= filter matches case-insensitively (search UX)."""
    create_experiment(name="tyre_pressure")
    create_experiment(name="brake_balance")

    response = client.get("/api/v1/experiments?name=TYRE")
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["name"] == "tyre_pressure"


# ============================================================================
# Get / Delete (no update — name is the immutable lake identity)
# ============================================================================


def test_get_experiment(
    create_experiment: ExperimentFactory, client: TestClient
) -> None:
    _, created = create_experiment(name="tyre_pressure")
    response = client.get(f"/api/v1/experiments/{created['experiment_id']}")
    assert response.status_code == 200
    assert response.json()["name"] == "tyre_pressure"


def test_get_experiment_not_found(client: TestClient) -> None:
    assert client.get("/api/v1/experiments/nonexistent").status_code == 404


def test_delete_experiment(
    create_experiment: ExperimentFactory, client: TestClient
) -> None:
    _, created = create_experiment()
    exp_id = created["experiment_id"]
    assert client.delete(f"/api/v1/experiments/{exp_id}").status_code == 200
    assert client.get(f"/api/v1/experiments/{exp_id}").status_code == 404


def test_delete_experiment_not_found(client: TestClient) -> None:
    assert client.delete("/api/v1/experiments/nonexistent").status_code == 404


def test_no_update_endpoint(
    create_experiment: ExperimentFactory, client: TestClient
) -> None:
    """Experiment name is immutable — there is no PUT route."""
    _, created = create_experiment()
    response = client.put(
        f"/api/v1/experiments/{created['experiment_id']}", json={"name": "renamed"}
    )
    assert response.status_code == 405  # method not allowed
