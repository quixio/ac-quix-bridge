"""Core read tools: get_test, get_session, list_logbook."""

from typing import Any

from pymongo.database import Database

from ....models import LogbookEntry, Test
from ...tests import resolve_test_names


def get_test(mongo: Database[dict[str, Any]], *, test_id: str) -> dict[str, Any]:
    """Fetch a Test with resolved display names."""
    doc = mongo.tests.find_one({"_id": test_id})
    if not doc:
        raise ValueError(f"Test {test_id} not found")
    test = resolve_test_names(Test(**doc), mongo)
    return test.model_dump(by_alias=False)


def get_session(
    mongo: Database[dict[str, Any]], *, test_id: str, session_id: str
) -> dict[str, Any]:
    """Fetch a single SessionInfo subdoc from a test."""
    doc = mongo.tests.find_one({"_id": test_id})
    if not doc:
        raise ValueError(f"Test {test_id} not found")
    for s in doc.get("sessions", []):
        if s["session_id"] == session_id:
            return dict(s)
    raise ValueError(f"session_id {session_id} not on test {test_id}")


def list_logbook(
    mongo: Database[dict[str, Any]],
    *,
    test_id: str,
    session_id: str | None = None,
    include_test_wide: bool = False,
) -> list[dict[str, Any]]:
    """List logbook entries for a test, optionally filtered by session.

    Ordering: ascending by created_at.
    """
    if session_id is not None:
        if include_test_wide:
            query: dict[str, Any] = {
                "test_id": test_id,
                "$or": [{"session_id": session_id}, {"session_id": None}],
            }
        else:
            query = {"test_id": test_id, "session_id": session_id}
    else:
        query = {"test_id": test_id}

    cursor = mongo.logbook.find(query).sort("created_at", 1)
    return [LogbookEntry(**doc).model_dump(by_alias=False) for doc in cursor]
