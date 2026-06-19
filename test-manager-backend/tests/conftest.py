import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Generator

import pytest
from fastapi.testclient import TestClient
from quixportal import get_filesystem
from testcontainers.mongodb import MongoDbContainer
from testcontainers.core.network import Network

from api.app import create_app

from tests.utils import find_free_port

TestFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
DeviceFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
EnvironmentFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
DriverFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]
ExperimentFactory = Callable[..., tuple[dict[str, Any], dict[str, Any]]]

PORTAL_API_PORT = find_free_port()


def _weasyprint_available() -> bool:
    """True if WeasyPrint's native libs (Pango/gobject) load on this host.

    Importing weasyprint triggers the gobject dlopen that fails on macOS
    unless DYLD_FALLBACK_LIBRARY_PATH points at brew's lib dir — see
    scripts/test-backend.sh. The container/cloud image always has the libs.
    """
    try:
        import weasyprint  # noqa: F401
    except (OSError, ImportError):
        return False
    return True


_WEASYPRINT_OK = _weasyprint_available()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_weasyprint: test renders a PDF; needs Pango/gobject native libs",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip PDF-rendering tests with a clear hint when the libs can't load."""
    if _WEASYPRINT_OK:
        return
    skip = pytest.mark.skip(
        reason="WeasyPrint native libs not loadable — run scripts/test-backend.sh "
        'or set DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib" (macOS)'
    )
    for item in items:
        if "requires_weasyprint" in item.keywords:
            item.add_marker(skip)


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
    """Load the standalone mock DCM app from mock_config_api/."""
    import importlib.util

    mock_main = Path(__file__).parent.parent.parent / "mock_config_api" / "main.py"
    spec = importlib.util.spec_from_file_location("mock_config_main", mock_main)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.app, mod.configs


@pytest.fixture()
def config_api(
    mock_config_app: tuple,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    app, configs = mock_config_app
    configs.clear()

    monkeypatch.setenv("CONFIG_API_URL", "http://test-mock")
    monkeypatch.setenv("Quix__Sdk__Token", "test")

    yield TestClient(app)


@pytest.fixture()
def client(
    mongo: None,
    blob_storage: None,
    config_api: TestClient,
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
def create_experiment(client: TestClient) -> ExperimentFactory:
    """Helper fixture to create an Experiment for testing."""

    def _create_experiment(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        input_data = {"name": "Test Experiment"}
        input_data.update(kwargs)
        response = client.post("/api/v1/experiments", json=input_data)
        assert response.status_code == 200
        return input_data, response.json()

    return _create_experiment


@pytest.fixture
def create_driver(client: TestClient) -> DriverFactory:
    """Helper fixture to create a Driver for testing."""

    def _create_driver(**kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        name = kwargs.get("name", "Test Driver")
        slug = name.lower().replace(" ", ".")
        input_data = {
            "name": name,
            "email": f"{slug}@example.com",
            "company": "Test Co",
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
