from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from settings import MongoSettings

_mongo: Database[dict[str, Any]]


def connect(settings: MongoSettings) -> Database[dict[str, Any]]:
    """Open the Mongo connection and ensure the track_layouts index.

    Uses the exact same MongoClient kwargs as
    test-manager-backend/api/mongo.py so the one-shot job behaves identically
    to the backend against the shared cluster.
    """
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

    # track_layouts collection — compound index is the telemetry join key
    # (AC static fields track + trackConfiguration). _id is already unique.
    # Index creation is idempotent.
    _mongo.track_layouts.create_index([("track", 1), ("trackConfiguration", 1)])

    return _mongo


def disconnect() -> None:
    _mongo.client.close()


def get_mongo() -> Database[dict[str, Any]]:
    return _mongo
