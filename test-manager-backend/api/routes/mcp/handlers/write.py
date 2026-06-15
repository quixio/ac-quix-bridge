"""Write tool: save_analysis (called by the agent at end of run)."""

import logging
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from ....models import Analysis, SaveAnalysisPayload
from ....notify import email_completed_analysis

logger = logging.getLogger(__name__)


_TERMINAL_STATUSES = {"complete", "failed"}


def save_analysis(
    mongo: Database[dict[str, Any]],
    *,
    analysis_id: str,
    summary_md: str,
    kpis: list[dict[str, Any]] | None = None,
    requirements_check: list[dict[str, Any]] | None = None,
    logbook_refs: list[str] | None = None,
    anomalies: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the agent's final analysis payload.

    Raises:
      - ValueError if analysis_id doesn't exist
      - ValueError if already complete/failed (no overwrite)
      - Pydantic ValidationError on bad payload
    """
    # Pydantic coerces the dict payloads into KpiValue/RequirementCheck/Anomaly.
    payload = SaveAnalysisPayload(
        analysis_id=analysis_id,
        summary_md=summary_md,
        kpis=kpis or [],  # ty: ignore[invalid-argument-type]
        requirements_check=requirements_check or [],  # ty: ignore[invalid-argument-type]
        logbook_refs=logbook_refs or [],
        anomalies=anomalies or [],  # ty: ignore[invalid-argument-type]
        extra=extra or {},
    )

    doc = mongo.analyses.find_one({"_id": analysis_id})
    if not doc:
        raise ValueError(f"Analysis {analysis_id} not found")
    if doc["status"] in _TERMINAL_STATUSES:
        raise ValueError(
            f"Analysis {analysis_id} already complete (status={doc['status']})"
        )

    update = {
        "kpis": [k.model_dump() for k in payload.kpis],
        "requirements_check": [r.model_dump() for r in payload.requirements_check],
        "logbook_refs": payload.logbook_refs,
        "anomalies": [a.model_dump() for a in payload.anomalies],
        "summary_md": payload.summary_md,
        "extra": payload.extra,
        "status": "complete",
        "updated_at": datetime.now(timezone.utc),
    }
    mongo.analyses.update_one({"_id": analysis_id}, {"$set": update})

    logger.info(
        "[mcp] save_analysis %s — kpis=%d reqs=%d anomalies=%d summary_md_len=%d",
        analysis_id,
        len(payload.kpis),
        len(payload.requirements_check),
        len(payload.anomalies),
        len(payload.summary_md),
    )

    # F4: email the completed report to the test's driver (best-effort).
    fresh = mongo.analyses.find_one({"_id": analysis_id})
    if fresh:
        email_completed_analysis(mongo, Analysis(**fresh))

    return {"ok": True, "analysis_id": analysis_id}
