import logging
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import OperationFailure

from .settings import MongoSettings
from .text import driver_name_key

logger = logging.getLogger(__name__)

_mongo: Database[dict[str, Any]]


def backfill_driver_name_keys(db: Database[dict[str, Any]]) -> int:
    """Set `name_key` on driver docs that predate the field. Idempotent.

    Runs every boot but no-ops once every doc has a key. Returns the number of
    docs updated. Must run before the unique `name_key` index is built so the
    index doesn't fail on duplicate nulls.
    """
    updated = 0
    for doc in db.drivers.find({"name_key": {"$exists": False}}):
        name = doc.get("name")
        if name:
            key = driver_name_key(name)
            db.drivers.update_one(
                {"_id": doc["_id"]},
                {"$set": {"name_key": key}},
            )
            logger.info("✓ backfilled name_key for %s: %r → %r", doc["_id"], name, key)
            updated += 1
        else:
            logger.warning("skipping driver %s: no name, name_key not set", doc["_id"])
    if updated:
        logger.info("✓ driver name_key backfill complete — %d updated", updated)
    return updated


def _safe_unique_index(db: Database[dict[str, Any]], field: str) -> None:
    """Build a partial-unique index on `field`, degrading on a duplicate-data
    or index-options conflict instead of crashing boot.

    The partial filter (`field` is a string) excludes legacy docs missing the
    field so duplicate nulls can't block the build. Only duplicate-key (11000)
    and index-options-conflict (85) failures degrade; anything else (e.g. auth)
    re-raises so a real misconfiguration is loud.
    """
    try:
        db.drivers.create_index(
            field,
            unique=True,
            partialFilterExpression={field: {"$type": "string"}},
        )
    except OperationFailure as exc:
        if exc.code not in (11000, 85):
            raise
        logger.warning(
            "Driver unique index on %s failed (degrading to app-level enforcement): %s",
            field,
            exc,
        )


def ensure_driver_indexes(db: Database[dict[str, Any]]) -> None:
    """Build driver indexes, failing soft on the uniqueness ones.

    The app-level 409 in `create_driver` is the primary uniqueness guard; the
    unique indexes are belt-and-suspenders. A pre-existing folded-name or email
    collision in the data must NOT crash boot (`connect()` has no error
    handling around it), so degrade and log instead.
    """
    db.drivers.create_index("name")
    db.drivers.create_index("created_at")
    db.drivers.create_index([("name", "text")])
    _safe_unique_index(db, "name_key")
    _safe_unique_index(db, "email")


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
    # F3 auto-trigger resolves test_id from a session_id (reverse of the bridge).
    _mongo.tests.create_index("sessions.session_id")

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

    # Drivers collection — backfill name_key before building its unique index.
    backfill_driver_name_keys(_mongo)
    ensure_driver_indexes(_mongo)

    # Logbook collection
    _mongo.logbook.create_index("test_id")
    _mongo.logbook.create_index([("test_id", 1), ("session_id", 1), ("created_at", -1)])

    # Analyses collection
    _mongo.analyses.create_index(
        [("test_id", 1), ("session_id", 1), ("created_at", -1)]
    )
    _mongo.analyses.create_index([("status", 1), ("updated_at", 1)])


def disconnect() -> None:
    _mongo.client.close()


def get_mongo() -> Database[dict[str, Any]]:
    return _mongo
