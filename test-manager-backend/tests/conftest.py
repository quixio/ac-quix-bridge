import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Generator, ContextManager

import httpx
import pytest
from fastapi.testclient import TestClient
from quixportal import get_filesystem
from testcontainers.mongodb import MongoDbContainer
from testcontainers.core.network import Network

from api.app import create_app
from api.settings import Settings, get_settings

from tests.utils import find_free_port

TestFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
DeviceFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
EnvironmentFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
DriverFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]

PORTAL_API_PORT = find_free_port()


@pytest.fixture(scope="session")
def portal_api_url() -> Generator[str, None, None]:
    """Start a mock portal API server for testing."""
    mock_server_path = Path(__file__).parent / "portal_api.py"
    process = subprocess.Popen(
        ["python3", str(mock_server_path), str(PORTAL_API_PORT)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.5)
    yield f"http://host.docker.internal:{PORTAL_API_PORT}"
    process.terminate()
    process.wait()


@pytest.fixture(scope="session")
def network() -> Generator[Network, None, None]:
    with Network() as network:
        yield network


@pytest.fixture(scope="session")
def mongo_container(network: Network) -> Generator[MongoDbContainer, None, None]:
    with MongoDbContainer(name="test-manager-mongo", network=network) as mongo:
        yield mongo


@pytest.fixture()
def mongo(
    monkeypatch: pytest.MonkeyPatch, mongo_container: MongoDbContainer
) -> Generator[None, None, None]:
    monkeypatch.setenv("MONGO_USER", mongo_container.username)
    monkeypatch.setenv("MONGO_PASSWORD", mongo_container.password)
    monkeypatch.setenv("MONGO_HOST", mongo_container.get_container_host_ip())
    monkeypatch.setenv("MONGO_PORT", str(mongo_container.get_exposed_port(27017)))
    monkeypatch.setenv("MONGO_DATABASE", mongo_container.dbname)
    client = mongo_container.get_connection_client()
    yield
    client.drop_database(mongo_container.dbname)
    client.close()


@pytest.fixture()
def blob_storage(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    storage_path = tmp_path_factory.mktemp("local_storage")
    monkeypatch.setenv("Quix__Workspace__Id", "test-workspace")
    monkeypatch.setenv(
        "Quix__BlobStorage__Connection__Json",
        f'''
        {{
            "provider": "local",
            "local_storage": {{
                "DirectoryPath": "{storage_path.absolute()}"
            }}
        }}
        ''',
    )


@pytest.fixture()
def fs(blob_storage: None) -> Any:
    return get_filesystem()


@pytest.fixture(scope="session")
def mock_config_app():
    """In-memory mock of the Dynamic Config Manager API.

    Supports the endpoints used by the test-manager-backend:
      POST   /api/v1/configurations          — create/replace config
      GET    /api/v1/configurations/{id}/content — latest version content
      GET    /api/v1/configurations/{id}/versions/{v}/content — specific version
      GET    /api/v1/configurations/{id}     — config metadata
      DELETE /api/v1/configurations/{id}/versions/{v} — delete version
    """
    from fastapi import Body, FastAPI, HTTPException

    app = FastAPI()
    configs: dict[str, dict] = {}  # id -> {metadata, versions: {v: content}}
    _next_id = [0]

    @app.post("/api/v1/configurations")
    def create_config(body: dict = Body(...)):
        metadata = body.get("metadata", {})
        content = body.get("content", {})
        replace = body.get("replace", False)

        # Find existing config by target_key + type if replace=True
        config_id = None
        if replace:
            for cid, cfg in configs.items():
                if (cfg["metadata"].get("target_key") == metadata.get("target_key")
                        and cfg["metadata"].get("type") == metadata.get("type")):
                    config_id = cid
                    break

        if config_id:
            cfg = configs[config_id]
            version = max(cfg["versions"].keys()) + 1
            cfg["versions"][version] = content
            cfg["metadata"]["version"] = version
        else:
            _next_id[0] += 1
            config_id = f"cfg-{_next_id[0]:04d}"
            version = 1
            configs[config_id] = {
                "metadata": {**metadata, "version": version},
                "versions": {version: content},
            }

        return {
            "data": {
                "id": config_id,
                "metadata": {**configs[config_id]["metadata"], "version": version},
            }
        }

    @app.get("/api/v1/configurations/{config_id}")
    def get_config(config_id: str):
        if config_id not in configs:
            raise HTTPException(status_code=404, detail="Not found")
        cfg = configs[config_id]
        return {"data": {"id": config_id, "metadata": cfg["metadata"]}}

    @app.get("/api/v1/configurations/{config_id}/content")
    def get_config_content(config_id: str):
        if config_id not in configs:
            raise HTTPException(status_code=404, detail="Not found")
        cfg = configs[config_id]
        latest = max(cfg["versions"].keys())
        return cfg["versions"][latest]

    @app.get("/api/v1/configurations/{config_id}/versions/{version}/content")
    def get_version_content(config_id: str, version: int):
        if config_id not in configs:
            raise HTTPException(status_code=404, detail="Not found")
        cfg = configs[config_id]
        if version not in cfg["versions"]:
            raise HTTPException(status_code=404, detail="Version not found")
        return cfg["versions"][version]

    @app.delete("/api/v1/configurations/{config_id}/versions/{version}")
    def delete_version(config_id: str, version: int):
        if config_id not in configs:
            raise HTTPException(status_code=404, detail="Not found")
        cfg = configs[config_id]
        if version not in cfg["versions"]:
            raise HTTPException(status_code=404, detail="Version not found")
        del cfg["versions"][version]
        if not cfg["versions"]:
            del configs[config_id]

    return app


@pytest.fixture()
def config_api(
    mock_config_app,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[httpx.Client, None, None]:
    # Create a test client for the mock config API
    mock_test_client = TestClient(mock_config_app)

    monkeypatch.setenv("CONFIG_API_URL", "http://test-mock")
    monkeypatch.setenv("Quix__Sdk__Token", "test")

    class MockConfigClient:
        """Wrapper that behaves like httpx.Client but uses TestClient."""

        def __init__(self, tc: TestClient):
            self._tc = tc

        def get(self, url: str):
            return self._tc.get(url)

        def post(self, url: str, json=None):
            return self._tc.post(url, json=json)

        def put(self, url: str, json=None):
            return self._tc.put(url, json=json)

        def delete(self, url: str):
            return self._tc.delete(url)

    yield MockConfigClient(mock_test_client)


@pytest.fixture()
def override_settings(
    client: TestClient,
) -> Callable[[int], ContextManager[None]]:
    @contextmanager
    def _override_settings(
        file_signature_expiration_seconds: int,
    ) -> Generator[None, None, None]:
        settings = Settings(  # type: ignore[call-arg]
            file_signature_expiration_seconds=file_signature_expiration_seconds,
        )
        app = client.app
        app.dependency_overrides[get_settings] = lambda: settings  # type: ignore[attr-defined]
        yield
        app.dependency_overrides.clear()  # type: ignore[attr-defined]

    return _override_settings


@pytest.fixture()
def client(
    mongo: None,
    blob_storage: None,
    config_api: httpx.Client,
    portal_api_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    from api.config_api import get_config_api_client

    monkeypatch.setenv("Quix__Portal__Api", portal_api_url)
    monkeypatch.setenv("API_AUTH_ACTIVE", "false")  # Disable auth for tests

    app = create_app()
    # Override the config API client dependency with our mock
    app.dependency_overrides[get_config_api_client] = lambda: config_api

    with TestClient(app) as c:
        yield c


@pytest.fixture
def create_device(client: TestClient) -> DeviceFactory:
    """Helper fixture to create a Device for testing."""
    def _create_device(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        input_data = {
            "category": "pc",
            "name": "Test PC",
            "status": "active",
        }
        input_data.update(kwargs)
        response = client.post("/api/v1/devices", json=input_data)
        assert response.status_code == 200
        return input_data, response.json()

    return _create_device


@pytest.fixture
def create_environment(client: TestClient) -> EnvironmentFactory:
    """Helper fixture to create an Environment for testing."""
    def _create_environment(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        input_data = {
            "name": "Test Environment",
            "location": "Test Location",
            "status": "active",
        }
        input_data.update(kwargs)
        response = client.post("/api/v1/environments", json=input_data)
        assert response.status_code == 200
        return input_data, response.json()

    return _create_environment


@pytest.fixture
def create_driver(client: TestClient) -> DriverFactory:
    """Helper fixture to create a Driver for testing."""
    def _create_driver(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        input_data = {
            "name": "Test Driver",
        }
        input_data.update(kwargs)
        response = client.post("/api/v1/drivers", json=input_data)
        assert response.status_code == 200
        return input_data, response.json()

    return _create_driver


@pytest.fixture
def create_test(
    client: TestClient,
    create_device: DeviceFactory,
    create_environment: EnvironmentFactory,
) -> TestFactory:
    """Helper fixture to create a Test for testing.

    Auto-creates a PC device, test rig device, and environment if not provided.
    """
    _counter = [0]

    def _create_test(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        _counter[0] += 1
        suffix = _counter[0]

        # Create dependencies if not provided
        if "pc_device_id" not in kwargs:
            _, pc = create_device(name=f"Test PC {suffix}", category="pc")
            kwargs["pc_device_id"] = pc["device_id"]

        if "test_rig_device_id" not in kwargs:
            _, rig = create_device(name=f"Test Rig {suffix}", category="test_rig")
            kwargs["test_rig_device_id"] = rig["device_id"]

        if "environment_id" not in kwargs:
            _, env = create_environment(name=f"Test Env {suffix}")
            kwargs["environment_id"] = env["environment_id"]

        input_data: dict[str, Any] = {
            "experiment_id": "test-experiment",
            "pc_device_id": kwargs.pop("pc_device_id"),
            "test_rig_device_id": kwargs.pop("test_rig_device_id"),
            "environment_id": kwargs.pop("environment_id"),
            "driver": "Test Driver",
            "requirements": "",
        }
        input_data.update(kwargs)
        response = client.post("/api/v1/tests", json=input_data)
        if response.status_code != 200:
            print(f"Response status: {response.status_code}")
            print(f"Response body: {response.text}")
        assert response.status_code == 200
        return input_data, response.json()

    return _create_test
