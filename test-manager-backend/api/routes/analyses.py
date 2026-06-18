"""CRUD routes for AI-generated session analyses."""

import asyncio
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pymongo.database import Database

from shared.post_race_ai.pdf import render_analysis_pdf
from shared.post_race_ai.runner import BatchAnalysisAI
from ..auth import bearer_from_request, read_permission, update_permission
from ..models import Analysis, AnalysisCreate, AnalysisRecipient, EmailSendResult
from ..mongo import get_mongo
from ..notify import (
    EmailNotConfigured,
    NoRecipientEmail,
    resolve_driver_email,
    send_analysis_email,
)

logger = logging.getLogger(__name__)

router = APIRouter()

IN_PROGRESS_STATUSES = ("pending", "running", "fetching", "analyzing", "saving")

# An in-progress analysis older than this is treated as stale (orphaned by a
# crash/restart, or a run that never started) — it no longer blocks a new run,
# so the UI can't get stuck "Analyzing…" forever. Well past the runner's 15-min
# hard timeout.
STALE_IN_PROGRESS_AFTER = timedelta(minutes=20)

# Hold strong refs to spawned tasks so Python's GC doesn't kill them
# mid-run (asyncio.create_task only holds a weak reference).
_RUNNING_TASKS: set[asyncio.Task[None]] = set()


@router.post(
    "/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        202: {"content": {"application/json": {"example": {"analysis_id": "..."}}}},
        200: {
            "description": "Existing analysis returned (auto dedup)",
            "content": {"application/json": {"example": {"analysis_id": "..."}}},
        },
    },
)
async def create_analysis(
    request: Request,
    response: Response,
    payload: AnalysisCreate = Body(...),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> dict[str, str]:
    # Gate the auto path to the trigger service. `triggered_by` is a
    # self-reported claim, and auto runs under the admin PAT_TOKEN (F6), so a
    # user could POST `auto` to dodge attribution/quota. When TRIGGER_SECRET is
    # set, an auto request must carry the matching X-Trigger-Secret header.
    # Unset (local dev / API_AUTH_ACTIVE=false) is a no-op, so manual flows and
    # tests are unaffected. Constant-time compare to avoid a timing oracle.
    if payload.triggered_by == "auto":
        expected = os.getenv("TRIGGER_SECRET", "")
        provided = request.headers.get("x-trigger-secret", "")
        if expected and not secrets.compare_digest(provided, expected):
            logger.warning(
                "[analyses] auto request rejected — missing/invalid X-Trigger-Secret"
            )
            raise HTTPException(status_code=403, detail="Not Allowed")

    if payload.test_id is not None:
        test = mongo.tests.find_one({"_id": payload.test_id})
        if not test:
            raise HTTPException(status_code=404, detail="Test not found")
        test_id = payload.test_id
        if payload.session_id is not None:
            known_session_ids = {s["session_id"] for s in test.get("sessions", [])}
            if payload.session_id not in known_session_ids:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"session_id '{payload.session_id}' not found on test {test_id}"
                    ),
                )
    else:
        # F3 auto-trigger path: resolve the owning test from session_id alone.
        # Membership is implicit — the test is found BY owning the session, so
        # no separate cross-check (unlike the test_id-supplied branch above).
        owners = list(
            mongo.tests.find({"sessions.session_id": payload.session_id}, {"_id": 1})
        )
        if not owners:
            raise HTTPException(
                status_code=404,
                detail=f"No test owns session '{payload.session_id}'",
            )
        if len(owners) > 1:
            logger.warning(
                "[analyses] session %s owned by %d tests; using first %s",
                payload.session_id,
                len(owners),
                owners[0]["_id"],
            )
        test_id = owners[0]["_id"]

    # Dedup against an already-running analysis for the same target so a second
    # click (a different user, tab, or the auto re-fire) returns the in-flight
    # run instead of spawning a duplicate. Auto additionally dedups a *complete*
    # run (the test-completed event re-emits for a session); a human may still
    # re-run a finished analysis manually. `failed` never blocks either path.
    # Non-atomic find-then-insert: two truly-simultaneous requests could both
    # miss — acceptable for seconds-apart human clicks; no atomic guard yet.
    # In-progress only blocks while it's fresh (a stale/orphaned run expires);
    # auto also dedups a completed run, at any age.
    fresh_cutoff = datetime.now(timezone.utc) - STALE_IN_PROGRESS_AFTER
    dedup_conds: list[dict[str, Any]] = [
        {
            "status": {"$in": list(IN_PROGRESS_STATUSES)},
            "created_at": {"$gte": fresh_cutoff},
        }
    ]
    if payload.triggered_by == "auto":
        dedup_conds.append({"status": "complete"})
    existing = mongo.analyses.find_one(
        {
            "test_id": test_id,
            "session_id": payload.session_id,
            "$or": dedup_conds,
        },
        sort=[("created_at", -1)],
    )
    if existing:
        logger.info(
            "[analyses] dedup — existing %s for (test=%s session=%s by=%s)",
            existing["_id"],
            test_id,
            payload.session_id,
            payload.triggered_by,
        )
        response.status_code = status.HTTP_200_OK
        return {"analysis_id": existing["_id"]}

    analysis_id = str(uuid4())
    now = datetime.now(timezone.utc)
    doc = Analysis(
        _id=analysis_id,
        test_id=test_id,
        session_id=payload.session_id,
        triggered_by=payload.triggered_by,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    mongo.analyses.insert_one(doc.model_dump(by_alias=True))
    logger.info(
        "[analyses] POST create %s (test=%s session=%s triggered_by=%s)",
        analysis_id,
        test_id,
        payload.session_id,
        payload.triggered_by,
    )

    # Spawn the async runner only when Quix.AI is configured. In tests the env
    # var is unset so the doc stays in `pending` (matches Phase 3 contract).
    if os.getenv("Quix__Portal__Api") and os.getenv("POST_RACE_AGENT_ID"):
        # Manual: attribute the Quix.AI session to the clicking user by
        # forwarding their bearer. Auto (Kafka trigger): no user — the runner
        # falls back to PAT_TOKEN. (The auto POST's own bearer is a service
        # token that cannot use Quix AI, so it must NOT be forwarded.)
        # TODO(F3): triggered_by is caller-supplied — gate "auto" to the
        # trigger service so a user can't opt into the admin PAT.
        bearer = (
            bearer_from_request(request) if payload.triggered_by == "manual" else None
        )
        analyzer = BatchAnalysisAI(mongo, quix_token=bearer)
        task = asyncio.create_task(
            analyzer.run(
                analysis_id=analysis_id,
                test_id=test_id,
                session_id=payload.session_id,
            )
        )
        _RUNNING_TASKS.add(task)
        task.add_done_callback(_RUNNING_TASKS.discard)
    else:
        logger.warning(
            "[analyses] runner not started — Quix__Portal__Api or "
            "POST_RACE_AGENT_ID unset (test or misconfig)"
        )
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


@router.get("/analyses/{analysis_id}/pdf")
def get_analysis_pdf(
    analysis_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> Response:
    """Render a completed analysis to a PDF (manual download; reused by F4)."""
    doc = mongo.analyses.find_one({"_id": analysis_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis = Analysis(**doc)
    if analysis.status != "complete":
        raise HTTPException(
            status_code=409,
            detail=f"Analysis not complete (status={analysis.status})",
        )
    pdf = render_analysis_pdf(analysis)
    safe_test_id = re.sub(r"[^A-Za-z0-9._-]", "_", analysis.test_id)
    filename = f"analysis-{safe_test_id}-{analysis_id[:8]}.pdf"
    logger.info("[analyses] GET %s/pdf -> %d bytes", analysis_id, len(pdf))
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/analyses/{analysis_id}/recipient",
    response_model=AnalysisRecipient,
    response_model_by_alias=False,
)
def get_analysis_recipient(
    analysis_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(read_permission),
) -> AnalysisRecipient:
    """Resolve the test driver's email for the manual-send confirmation dialog."""
    doc = mongo.analyses.find_one({"_id": analysis_id}, {"test_id": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    email = resolve_driver_email(mongo, doc["test_id"])
    return AnalysisRecipient(email=email, has_email=bool(email))


@router.post(
    "/analyses/{analysis_id}/email",
    response_model=EmailSendResult,
    response_model_by_alias=False,
)
def send_analysis_email_route(
    analysis_id: str,
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
    _: None = Depends(update_permission),
) -> EmailSendResult:
    """Manually email a completed analysis PDF to the test's driver."""
    doc = mongo.analyses.find_one({"_id": analysis_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Analysis not found")
    analysis = Analysis(**doc)
    if analysis.status != "complete":
        raise HTTPException(
            status_code=409,
            detail=f"Analysis not complete (status={analysis.status})",
        )
    try:
        email = send_analysis_email(mongo, analysis)
    except EmailNotConfigured as exc:
        raise HTTPException(
            status_code=503, detail="Email is not configured on the server"
        ) from exc
    except NoRecipientEmail as exc:
        raise HTTPException(
            status_code=422, detail="The test's driver has no email on file"
        ) from exc
    except Exception as exc:  # smtplib/render failure — surface, don't swallow
        logger.error("[analyses] manual email failed %s: %s", analysis_id, exc)
        raise HTTPException(status_code=502, detail="Failed to send the email") from exc
    logger.info("[analyses] POST %s/email -> sent to %s", analysis_id, email)
    return EmailSendResult(sent=True, email=email)


@router.get("/analyses")
def list_analyses(
    test_id: str | None = None,
    session_id: str | None = None,
    session_id_is_null: bool | None = None,
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
    elif session_id_is_null is True:
        # Match docs with null session_id (test-wide) OR no session_id field
        # (defensive against legacy/migrated docs missing the field).
        # session_id_is_null=False is a no-op — only True triggers filtering.
        query["session_id"] = {"$in": [None]}
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
        "[analyses] GET list (test_id=%s session_id=%s session_id_is_null=%s status=%s) -> %d/%d",
        test_id,
        session_id,
        session_id_is_null,
        status_filter,
        len(items),
        total,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
