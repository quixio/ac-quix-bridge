from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from .settings import MongoSettings

_mongo: Database[dict[str, Any]]


def connect(settings: MongoSettings) -> None:
    global _mongo
    _mongo = MongoClient(
        settings.url,
        tz_aware=True,
        uuidRepresentation="standard",
        maxPoolSize=50,
        minPoolSize=10,
        maxIdleTimeMS=60000,
        connectTimeoutMS=5000,
        serverSelectionTimeoutMS=5000,
    ).get_database(settings.database)

    # Tests collection
    _mongo.tests.create_index("experiment_id")
    _mongo.tests.create_index("environment_id")
    _mongo.tests.create_index("driver")
    _mongo.tests.create_index("status")
    _mongo.tests.create_index("pc_device_id")
    _mongo.tests.create_index("test_rig_device_id")
    _mongo.tests.create_index([("experiment_id", "text"), ("driver", "text")])

    # Devices collection
    _mongo.devices.create_index("category")
    _mongo.devices.create_index("status")
    _mongo.devices.create_index("name")
    _mongo.devices.create_index([("name", "text")])

    # Environments collection
    _mongo.environments.create_index("name")
    _mongo.environments.create_index("status")
    _mongo.environments.create_index("location")
    _mongo.environments.create_index([("name", "text"), ("location", "text")])

    # Drivers collection
    _mongo.drivers.create_index("name")
    _mongo.drivers.create_index("created_at")
    _mongo.drivers.create_index([("name", "text")])

    # Logbook collection
    _mongo.logbook.create_index("test_id")
    _mongo.logbook.create_index([("test_id", 1), ("session_id", 1), ("created_at", -1)])


def disconnect() -> None:
    _mongo.client.close()


def get_mongo() -> Database[dict[str, Any]]:
    return _mongo
