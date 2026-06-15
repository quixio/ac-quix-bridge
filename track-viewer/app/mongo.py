"""Read-only Mongo access for the track viewer.

This module NEVER writes: no create_index, no insert/update/delete. It mirrors
the MongoClient kwargs used by test-manager-backend/api/mongo.py and
track-importer/mongo.py so the viewer behaves identically against the shared
cluster, with `serverSelectionTimeoutMS=5000` so an unreachable Mongo fails
fast instead of hanging the request.

MongoClient construction is lazy (no socket opened until the first operation),
so connect() cannot crash on an unreachable host. Callers must guard actual
operations (ping, find, count) and surface failures through /healthz and the
UI — that graceful "cannot reach Mongo" path is the verification signal.
"""

from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from .settings import MongoSettings

_client: MongoClient[dict[str, Any]] | None = None
_mongo: Database[dict[str, Any]] | None = None


def connect(settings: MongoSettings) -> None:
    """Construct the (lazy) Mongo client and bind the target database.

    Read-only: deliberately does NOT create any index or touch any document.
    """
    global _client, _mongo
    _client = MongoClient(
        settings.url,
        tz_aware=True,
        uuidRepresentation="standard",
        maxPoolSize=50,
        minPoolSize=10,
        maxIdleTimeMS=60000,
        connectTimeoutMS=5000,
        serverSelectionTimeoutMS=5000,
    )
    _mongo = _client.get_database(settings.database)


def disconnect() -> None:
    global _client, _mongo
    if _client is not None:
        _client.close()
    _client = None
    _mongo = None


def get_mongo() -> Database[dict[str, Any]]:
    if _mongo is None:
        raise RuntimeError("Mongo not connected; call connect() first")
    return _mongo
