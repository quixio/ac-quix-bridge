"""Historical session lookups for baseline / cross-session comparison."""

from typing import Any

from pymongo.database import Database


def list_sessions_for_test(
    mongo: Database[dict[str, Any]], *, test_id: str
) -> list[dict[str, Any]]:
    """All sessions on a test, sorted descending by session_id (latest first)."""
    doc = mongo.tests.find_one({"_id": test_id})
    if not doc:
        raise ValueError(f"Test {test_id} not found")
    sessions = [dict(s) for s in doc.get("sessions", [])]
    return sorted(sessions, key=lambda s: s["session_id"], reverse=True)


def list_recent_sessions_for_driver(
    mongo: Database[dict[str, Any]],
    *,
    driver_id: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Flat list of recent sessions across all tests for a driver.

    `driver_id` is the Driver._id (e.g. "DRV-0007"); resolved here to the
    free-text name stored on Test.driver because that's what tests carry.

    Each item: {test_id, session_id, track, car_model, created_at}.
    Capped at min(limit, 20).
    """
    driver_doc = mongo.drivers.find_one({"_id": driver_id})
    if not driver_doc:
        raise ValueError(f"Driver {driver_id} not found")
    driver_name = driver_doc["name"]

    limit = max(1, min(limit, 20))

    pipeline: list[dict[str, Any]] = [
        {"$match": {"driver": driver_name}},
        {"$unwind": "$sessions"},
        {
            "$project": {
                "_id": 0,
                "test_id": "$_id",
                "session_id": "$sessions.session_id",
                "track": "$sessions.track",
                "car_model": "$sessions.car_model",
                "created_at": "$created_at",
            }
        },
        {"$sort": {"session_id": -1}},
        {"$limit": limit},
    ]
    return list(mongo.tests.aggregate(pipeline))
