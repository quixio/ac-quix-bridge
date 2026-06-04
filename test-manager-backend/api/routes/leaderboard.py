"""Multi-driver live-positions leaderboard.

One endpoint:

  * `GET /api/v1/leaderboard/live-positions` — returns a flat list of
    `LivePositionEntry` rows: up to 5 per (track, car, experiment)
    group, sorted by group then rank. Polled at ~3.5 s by the frontend.

Delegates to `leaderboard_real` which queries the Quix Lakehouse +
the in-process live-telemetry consumer.

See `docs/architecture-leaderboard-live-positions.md` for the full
design (file pending — see dev-planning/leaderboard-consolidated/spec.md).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from ..auth import read_permission
from ..models import LivePositionEntry
from ..mongo import get_mongo
from . import leaderboard_real

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# ---------------------------------------------------------------------------
# /live-positions
# ---------------------------------------------------------------------------


@router.get("/live-positions", response_model=list[LivePositionEntry])
async def get_live_positions(
    _auth: None = Depends(read_permission),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
) -> list[LivePositionEntry]:
    """Return the full multi-driver leaderboard.

    Real mode: Lakehouse-driven historicals + live driver from
    `live_telemetry.get_active_driver()`, assembled in
    `leaderboard_real.build_live_positions()`.
    """
    try:
        rows = leaderboard_real.build_live_positions(mongo)
    except leaderboard_real.LeaderboardError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return [LivePositionEntry(**row) for row in rows]
