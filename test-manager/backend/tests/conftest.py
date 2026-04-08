import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator, ContextManager

import httpx
import requests
from influxdb import InfluxDBClient
import pytest
from fastapi.testclient import TestClient
from quixportal import get_filesystem
from testcontainers.mongodb import MongoDbContainer
from testcontainers.kafka import KafkaContainer
from testcontainers.influxdb import InfluxDbContainer
from testcontainers.core.generic import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.waiting_utils import wait_for_logs

from api.app import create_app
from api.settings import Settings, get_settings

from tests.utils import find_free_port

TestFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]

PORTAL_API_PORT = find_free_port()
CONFIG_API_PORT = find_free_port()


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
def kafka_container(network: Network) -> Generator[KafkaContainer, None, None]:
    with KafkaContainer(name="test-manager-kafka", network=network) as kafka:
        yield kafka


@pytest.fixture(scope="session")
def mock_config_app():
    """
    Get the mock configuration API app for testing.

    Uses the shared mock implementation from mock_config_api.main
    to avoid code duplication between dev and test environments.
    """
    import sys
    from pathlib import Path

    # Add mock_config_api directory to Python path
    mock_config_api_dir = Path(__file__).parent.parent.parent / "mock_config_api"
    sys.path.insert(0, str(mock_config_api_dir))

    try:
        from main import app
        return app
    finally:
        # Clean up sys.path
        sys.path.remove(str(mock_config_api_dir))


@pytest.fixture()
def config_api(
    mock_config_app,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[httpx.Client, None, None]:
    from fastapi.testclient import TestClient
    
    # Create a test client for the mock config API
    test_client = TestClient(mock_config_app)
    
    # Mock the CONFIG_API_URL environment variable
    monkeypatch.setenv("CONFIG_API_URL", "http://test-mock")
    monkeypatch.setenv("Quix__Sdk__Token", "test")
    
    # Create a wrapper that behaves like httpx.Client but uses TestClient
    class MockConfigClient:
        def __init__(self, test_client):
            self._test_client = test_client
        
        def get(self, url: str):
            response = self._test_client.get(url)
            # Store the original json method to avoid recursion
            original_json = response.json
            response.json = lambda: original_json()
            return response
        
        def post(self, url: str, json=None):
            response = self._test_client.post(url, json=json)
            original_json = response.json
            response.json = lambda: original_json()
            return response
        
        def put(self, url: str, json=None):
            response = self._test_client.put(url, json=json)
            original_json = response.json
            response.json = lambda: original_json()
            return response
        
        def delete(self, url: str):
            response = self._test_client.delete(url)
            original_json = response.json
            response.json = lambda: original_json()
            return response

    client = MockConfigClient(test_client)
    yield client
    
    # No cleanup needed for in-memory mock


@pytest.fixture(scope="session")
def influx_container(network: Network) -> Generator[InfluxDbContainer, None, None]:
    env = {
        "INFLUXDB_ADMIN_USER": "test",
        "INFLUXDB_ADMIN_PASSWORD": "test",
    }
    with InfluxDbContainer(
        "influxdb:1.11",
        name="test-manager-influx",
        env=env,
        network=network,
    ) as influx:
        yield influx


@pytest.fixture()
def influx(
    monkeypatch: pytest.MonkeyPatch, influx_container: InfluxDbContainer
) -> Generator[InfluxDBClient, None, None]:
    host = influx_container.get_container_host_ip()
    port = str(influx_container.get_exposed_port(8086))
    user = influx_container.env["INFLUXDB_ADMIN_USER"]
    password = influx_container.env["INFLUXDB_ADMIN_PASSWORD"]
    database = "test_manager"

    monkeypatch.setenv("INFLUXDB_HOST", host)
    monkeypatch.setenv("INFLUXDB_PORT", port)
    monkeypatch.setenv("INFLUXDB_USER", user)
    monkeypatch.setenv("INFLUXDB_PASSWORD", password)

    _client = InfluxDBClient(
        host=host,
        port=port,
        username=user,
        password=password,
        database=database,
    )

    yield _client

    for measurement in _client.get_list_measurements():
        _client.query(f'DROP SERIES FROM "{measurement["name"]}"')
    _client.close()


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
    influx: InfluxDBClient,
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
def create_dac(client: TestClient) -> Callable[..., tuple[dict[str, Any], dict[str, Any]]]:
    """Helper fixture to create a Device for testing."""
    def _create_dac(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        input_data = {
            "device_id": "dac1",
            "manufacturer": "Acme Corp",
            "product_category": "WP",
            "product_name": "Vitocal 200-S",
            "sample_type": "PFP",
            "sample_nr": "1",
            "location": "Bench 3",
            "creator": "Test User",
        }
        input_data.update(kwargs)
        response = client.post("/api/v1/devices", json=input_data)
        assert response.status_code == 200
        return input_data, response.json()

    return _create_dac


@pytest.fixture
def create_test(client: TestClient, create_dac: Callable[..., tuple[dict[str, Any], dict[str, Any]]]) -> TestFactory:
    def _create_test(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        # Create a Device first if not provided
        if "devices" not in kwargs:
            # Use test_id to create unique Device ID, or fallback to provided device_id
            test_id = kwargs.get("test_id", "test1")
            device_id = kwargs.get("device_id", f"device-for-{test_id}")
            _, created_dac = create_dac(device_id=device_id)
            kwargs["devices"] = [{"device_id": device_id, "device_version": None}]

        input_data = {
            "test_id": "test1",
            "campaign_id": "campaign1",
            "environment_id": "tec1",
            "operator": "John Doe",
            "status": "draft",
            "start": datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc).isoformat(),
            "end": datetime(2024, 1, 15, 16, 45, 0, tzinfo=timezone.utc).isoformat(),
            "sensors": {
                "T1100": {
                    "mp": "t_A_ODU_out_1",
                    "unit": "°C",
                    "description": "Temperatur 1 Kammer",
                    "sensor_id": "P110884",
                    "type": "AI",
                    "source": "EPE",
                    "csv_col": "AI_T1100",
                }
            },
        }
        input_data.update(kwargs)
        response = client.post("/api/v1/tests", json=input_data)
        if response.status_code != 200:
            print(f"Response status: {response.status_code}")
            print(f"Response body: {response.text}")
        assert response.status_code == 200
        return input_data, response.json()

    return _create_test
