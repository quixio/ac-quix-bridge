"""Read-only Mongo access for the telemetry explorer.

Copied from track-viewer/app/mongo.py (kept as a local copy rather than a
cross-import because the apps have separate Docker build contexts). This module
NEVER writes: no create_index, no insert/update/delete. MongoClient kwargs
mirror test-manager-backend/track-importer so the explorer behaves identically
against the shared cluster, with `serverSelectionTimeoutMS=5000` so an
unreachable Mongo fails fast instead of hanging the request.

MongoClient construction is lazy (no socket opened until the first operation),
so connect() cannot crash on an unreachable host. Callers MUST guard actual
operations (find_one, count) and surface failures by falling back to the
bundled CSV (see track_loader.py) — that graceful "cannot reach Mongo" path is
the verification signal.
"""

from __future__ import annotations

from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from mongo_settings import MongoSettings

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
