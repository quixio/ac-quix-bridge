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
        maxPoolSize=50,  # Allow more concurrent connections
        minPoolSize=10,  # Keep connections warm
        maxIdleTimeMS=60000,  # Reuse connections for 60s
        connectTimeoutMS=5000,  # Fail fast on connection issues
        serverSelectionTimeoutMS=5000,  # Fail fast on server selection
    ).get_database(settings.database)

    # Create indexes for optimal query performance
    # Tests collection

    # Drop obsolete Phase 1 indexes
    try:
        _mongo.tests.drop_index("sample_id_1")
    except Exception:
        pass
    try:
        _mongo.tests.drop_index("environment_id_1")
    except Exception:
        pass
    try:
        _mongo.tests.drop_index("test_id_text_campaign_id_text_sample_id_text_environment_id_text_operator_text_description_text")
    except Exception:
        pass

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

    # Device Journal collection
    _mongo.device_journal.create_index("device_id")
    _mongo.device_journal.create_index("timestamp")
    _mongo.device_journal.create_index([("device_id", 1), ("timestamp", -1)])  # Compound index

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


def disconnect() -> None:
    _mongo.client.close()


def get_mongo() -> Database[dict[str, Any]]:
    return _mongo
