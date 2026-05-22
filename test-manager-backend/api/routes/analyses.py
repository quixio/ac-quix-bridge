"""CRUD routes for AI-generated session analyses."""

import logging
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pymongo.database import Database

from ..auth import read_permission, update_permission
from ..models import Analysis, AnalysisCreate
from ..mongo import get_mongo

logger = logging.getLogger(__name__)

router = APIRouter()

IN_PROGRESS_STATUSES = ("pending", "running", "fetching", "analyzing", "saving")


def _spawn_runner_stub(analysis_id: str, test_id: str, session_id: str) -> None:
    """Placeholder for the async runner. Real impl lands in Phase 5.

    Keeps the doc in `pending` state — the test confirms this behaviour.
    Phase 5 swaps this for asyncio.create_task(run_analysis(...)).
    """
    logger.info(
        "[analyses] runner spawn DEFERRED (Phase 5): analysis=%s test=%s session=%s",
        analysis_id,
        test_id,
        session_id,
    )


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"content": {"application/json": {"example": {"analysis_id": "..."}}}}
    },
)
def create_analysis(
    payload: AnalysisCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    test = mongo.tests.find_one({"_id": payload.test_id})
    if not test:
        raise HTTPException(status_code=404, detail="Test not found")

    known_session_ids = {s["session_id"] for s in test.get("sessions", [])}
    if payload.session_id not in known_session_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"session_id '{payload.session_id}' not found on test {payload.test_id}"
            ),
        )

    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc)
    doc = Analysis(
        _id=analysis_id,
        test_id=payload.test_id,
        session_id=payload.session_id,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    mongo.analyses.insert_one(doc.model_dump(by_alias=True))
    logger.info(
        "[analyses] POST create %s (test=%s session=%s)",
        analysis_id,
        payload.test_id,
        payload.session_id,
    )

    _spawn_runner_stub(analysis_id, payload.test_id, payload.session_id)
    return {"analysis_id": analysis_id}


@router.get(
    "/analyses/{analysis_id}", response_model=Analysis, response_model_by_alias=False
)
def get_analysis(
    analysis_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Analysis:
    doc = mongo.analyses.find_one({"_id": analysis_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    logger.debug("[analyses] GET %s", analysis_id)
    return Analysis(**doc)


@router.get("/analyses")
def list_analyses(
    test_id: str | None = None,
    session_id: str | None = None,
    status_filter: Literal["complete", "failed", "in_progress"] | None = Query(
        default=None, alias="status"
    ),
    page: int = 1,
    page_size: int = 20,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if test_id is not None:
        query["test_id"] = test_id
    if session_id is not None:
        query["session_id"] = session_id
    if status_filter is not None:
        if status_filter == "in_progress":
            query["status"] = {"$in": list(IN_PROGRESS_STATUSES)}
        else:
            query["status"] = status_filter

    total = mongo.analyses.count_documents(query)
    skip = max(0, (page - 1) * page_size)
    cursor = (
        mongo.analyses.find(query).sort("created_at", -1).skip(skip).limit(page_size)
    )
    items = [Analysis(**doc).model_dump(by_alias=False) for doc in cursor]

    logger.debug(
        "[analyses] GET list (test_id=%s session_id=%s status=%s) -> %d/%d",
        test_id,
        session_id,
        status_filter,
        len(items),
        total,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
