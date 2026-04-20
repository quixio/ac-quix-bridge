"""
Leaderboard routes — best-lap aggregation served from the shared QuixLake.

The endpoint runs a single SQL aggregation against the lake's `ac_telemetry`
table via `QuixLakeClient.query()` from the `quixlake-sdk` package and returns
the full per-(track, car, experiment, driver) best-lap matrix. The frontend
fetches once on tab mount and derives the Track/Car/Experiment dropdowns +
filters client-side.

Round 5 (sc-71954): Round 4 fixed the wire protocol (SDK instead of raw
httpx) and the endpoint started returning 200. But the response was an
empty array even though the lake contained session data (confirmed via
Telemetry Explorer, which reads the same `ac_telemetry` table). Root
cause: the previous SQL aggregated `MAX(iLastTime)` per lap partition and
required `lap > 1`. `iLastTime` is AC's "last completed lap time" field
from the Graphics struct — it's 0 until the very first lap is complete
and stays 0 across all rows of short sessions that never complete a lap.
Combined with the `lap > 1` filter (which dropped all lap-1 rows), any
session with < 2 completed laps produced zero rows. Fix: compute lap time
from `MAX(timestamp_ms) - MIN(timestamp_ms)` within each lap partition,
keep lap >= 1, and guard against single-row partitions with
`COUNT(*) >= 2`. Lap 1 now includes the rollout from the grid/pit, but
short test sessions stop being invisible.

Round 4 (sc-71954): Round 3 rewired the endpoint to read `QUIXLAKE_URL` and
`QUIX_LAKE_TOKEN` directly from env, but kept a raw `httpx` call against
`{quixlake_url}/api/query`. That path is served by the separate Query UI
service, which is not deployed in the `acquixbridge-leaderboard` env — so
the deployed backend returned 404 HTML. The fix is to use `QuixLakeClient`
from `quixlake-sdk` (the same pattern as `telemetry-comparison/main.py:23,
72-73, 114`), which abstracts away the wire protocol and returns a pandas
DataFrame.

Round 3 (sc-71954): the lake connection no longer goes through the Settings UI
`measurements_deployment` reference or a hardcoded `_FALLBACK_MEASUREMENTS_URL`.
Instead, the endpoint reads `QUIXLAKE_URL` and `QUIX_LAKE_TOKEN` directly from
env — the same pattern used by the Telemetry Explorer deployment
(`quix.yaml:534-544`), which talks to the same shared lake. The previous
fall-through resolved to a URL in the wrong workspace and upstream returned
403 Access Forbidden.

Driver names are rewritten on the server to the display-case form stored in
the Mongo `drivers.name` collection. The lake partitions `driver` in
lowercase (see `tests.py:128` — `test_data.driver.lower()`), so a Mongo
lookup is the canonical way to recover proper casing. Unmatched drivers
keep their raw lowercase value rather than being dropped.
"""

import logging
import os
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database
from quixlake import QuixLakeClient

from ..auth import read_permission
from ..models import BestLapEntry
from ..mongo import get_mongo
from ..settings import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# Dummy leaderboard rows returned when LOCAL_DEV_MODE=true. Lets the full UI
# (dropdowns + table + rank) render end-to-end without the data-lake stack.
# Matrix: 3 tracks × 2 cars × 2 experiments × 5 drivers = 60 rows, lap times
# jittered so the per-(track, car, experiment) ranking is non-trivial.
_LOCAL_DEV_TRACKS = ["ks_nurburgring", "spa", "silverstone"]
_LOCAL_DEV_CARS = ["bmw_1m", "ferrari_488"]
_LOCAL_DEV_EXPERIMENTS = ["baseline", "tuned"]
_LOCAL_DEV_DRIVERS = ["Ludvík", "Alice", "Bob", "Carla", "Diego"]


def _make_local_dev_rows() -> list[BestLapEntry]:
    rows: list[BestLapEntry] = []
    for t_idx, track in enumerate(_LOCAL_DEV_TRACKS):
        for c_idx, car in enumerate(_LOCAL_DEV_CARS):
            for e_idx, experiment in enumerate(_LOCAL_DEV_EXPERIMENTS):
                for d_idx, driver in enumerate(_LOCAL_DEV_DRIVERS):
                    # Deterministic but varied lap times — base per track/car,
                    # tuned experiments ~1.5s faster, driver skill adds spread.
                    base_ms = 90000 + t_idx * 4000 + c_idx * 2500
                    exp_offset = -1500 if experiment == "tuned" else 0
                    driver_offset = d_idx * 420 + (d_idx * d_idx * 37)
                    rows.append(
                        BestLapEntry(
                            track=track,
                            car=car,
                            experiment=experiment,
                            driver=driver,
                            best_lap_ms=base_ms + exp_offset + driver_offset,
                        )
                    )
    return rows


# The lake's Hive partitions are (environment, test_rig, experiment, driver,
# track, carModel, session_id, lap) — see quix.yaml:127.
#
# Aggregation strategy (Round 5 — timestamp-delta based):
#   Compute per-lap duration as MAX(timestamp_ms) - MIN(timestamp_ms) within
#   each (track, carModel, experiment, driver, session_id, lap) partition,
#   then take MIN across sessions for the per-driver best. `timestamp_ms` is
#   the same millisecond wall-clock column that Telemetry Explorer queries
#   against this table (see telemetry-comparison/main.py:123, 192).
#
#   Guards: `lap >= 1` (keep lap 1 so short sessions are not invisible —
#   trade-off: lap 1 includes the rollout from grid/pit and may bias the
#   "best" slow for single-lap sessions, acceptable for V1). `COUNT(*) >= 2`
#   + `MAX(timestamp_ms) > MIN(timestamp_ms)` drops lap partitions with
#   only one sample, which would otherwise produce a 0-ms lap time and
#   dominate the MIN.
#
#   The previous Round-4 SQL used `MAX(iLastTime)` (AC Graphics struct's
#   "last completed lap time") with `lap > 1` + `HAVING MAX(iLastTime) > 0`.
#   That combination returned empty for sessions that never completed ≥ 2
#   laps (iLastTime is 0 until the very first lap is complete), which is
#   the common case for short test-rig runs.
_BEST_LAPS_SQL = """
WITH per_lap AS (
  SELECT
    track,
    carModel,
    experiment,
    driver,
    session_id,
    lap,
    MAX(timestamp_ms) - MIN(timestamp_ms) AS lap_time_ms,
    COUNT(*) AS sample_count
  FROM ac_telemetry
  WHERE lap >= 1
  GROUP BY track, carModel, experiment, driver, session_id, lap
  HAVING MAX(timestamp_ms) > MIN(timestamp_ms)
     AND COUNT(*) >= 2
)
SELECT
  track,
  carModel,
  experiment,
  driver,
  MIN(lap_time_ms) AS best_lap_ms
FROM per_lap
GROUP BY track, carModel, experiment, driver
ORDER BY track, carModel, experiment, best_lap_ms ASC
""".strip()


def _fold_driver_name(name: str) -> str:
    """Fold a driver name to a diacritic-insensitive lowercase ASCII key.

    The lake partitions `driver` by pushing the raw Python `str.lower()`
    through the Dynamic Config Manager (see `routes/tests.py:128`), which is
    diacritic-preserving (`"Ludvík".lower() == "ludvík"`, codepoint 0x00ED,
    NOT `"ludvik"` ASCII). But in practice, users at test-creation time
    typically type driver IDs without diacritics — so a Mongo document with
    `"Ludvík"` needs to match a lake partition value of `"ludvik"`.

    We normalise to NFKD and strip combining marks so `"Ludvík"` and
    `"ludvik"` both fold to `"ludvik"`. Spec §7.1's ludvík↔ludvik example
    requires this behaviour.

    Edge case: if the name is entirely non-ASCII (e.g. `"李"`), NFKD + ASCII
    fold yields an empty string. Fall back to a plain `.lower()` so the key
    is still unique and the row isn't dropped.
    """
    if not name:
        return ""
    folded = (
        unicodedata.normalize("NFKD", name)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    if not folded:
        # Non-ASCII name (e.g. CJK) folded to empty — keep the original
        # lowercased form as the key so the entry remains lookup-able.
        return name.lower()
    return folded


def _build_driver_name_lookup(mongo: Database[dict[str, Any]]) -> dict[str, str]:
    """Build a {folded_name: display_name} map from the Mongo drivers
    collection. O(unique drivers) — one call per aggregation.

    The key is produced by `_fold_driver_name` (NFKD + ASCII fold + lower)
    so diacritic-bearing Mongo names (e.g. `"Ludvík"`) match ASCII-only
    lake partition values (e.g. `"ludvik"`).
    """
    lookup: dict[str, str] = {}
    for doc in mongo.drivers.find({}, {"name": 1}):
        name = doc.get("name")
        if isinstance(name, str) and name:
            lookup[_fold_driver_name(name)] = name
    return lookup


@router.get("/best-laps", response_model=list[BestLapEntry])
async def get_best_laps(
    _auth: None = Depends(read_permission),
    mongo: Database[dict[str, Any]] = Depends(get_mongo),
) -> list[BestLapEntry]:
    """Return the best lap per (track, car, experiment, driver).

    V1: one SQL query, no query params, full matrix returned. Frontend
    derives filter options from distinct partition values in the payload
    and filters client-side when the user changes a dropdown.
    """
    if os.getenv("LOCAL_DEV_MODE") == "true":
        logger.info("LOCAL_DEV_MODE=true — returning dummy leaderboard rows.")
        return _make_local_dev_rows()

    settings = get_settings()

    # Round 3: read QuixLake config directly from env. No more Settings-UI
    # deployment-ref fallback and no hardcoded fallback URL — both were
    # pointing at the wrong workspace on the `acquixbridge-leaderboard` env
    # and causing upstream 403s.
    if not settings.quixlake_url or not settings.quix_lake_token:
        raise HTTPException(
            status_code=500,
            detail="QuixLake URL/token not configured",
        )

    # Round 4: use the quixlake-sdk client instead of raw HTTP against
    # `/api/query`. That path belongs to Query UI (a separate service not
    # deployed in this env). `QuixLakeClient.query()` is synchronous and
    # returns a pandas DataFrame — same usage pattern as
    # telemetry-comparison/main.py:114. FastAPI is happy running a sync
    # block inside an async handler for this I/O volume.
    try:
        client = QuixLakeClient(
            base_url=settings.quixlake_url,
            token=settings.quix_lake_token,
        )
        logger.info("Querying QuixLake for best laps via QuixLakeClient.")
        df = client.query(_BEST_LAPS_SQL)
        rows: list[dict[str, Any]] = df.to_dict("records")
        logger.info("Best-laps aggregation returned %d rows", len(rows))
    except Exception as e:
        logger.exception("QuixLake query failed")
        raise HTTPException(
            status_code=500,
            detail=f"QuixLake query error: {e}",
        )

    # Build the driver display-case lookup from Mongo. One call per
    # aggregation — trivial.
    driver_name_lookup = _build_driver_name_lookup(mongo)

    entries: list[BestLapEntry] = []
    for row in rows:
        # Empty or malformed cells → skip the row; the SQL is static, so
        # this should only happen if the upstream schema drifts.
        raw_best = row.get("best_lap_ms")
        if raw_best is None or raw_best == "":
            continue
        try:
            best_lap_ms = int(float(raw_best))
        except (TypeError, ValueError):
            logger.warning("Skipping row with non-numeric best_lap_ms: %r", row)
            continue

        raw_driver = row.get("driver") or ""
        # Fold the lake value through the same diacritic-insensitive key
        # function used to build the lookup, so an ASCII lake `"ludvik"`
        # matches a Mongo `"Ludvík"`. Fallback: if the Mongo lookup misses,
        # keep the raw (lowercase) lake value. Do not drop the row.
        display_driver = driver_name_lookup.get(
            _fold_driver_name(raw_driver), raw_driver
        )

        entries.append(
            BestLapEntry(
                track=row.get("track") or "",
                # Rename lake's `carModel` → public `car` to keep the
                # response contract tidy. The lake schema leak doesn't
                # need to propagate to the frontend.
                car=row.get("carModel") or "",
                experiment=row.get("experiment") or "",
                driver=display_driver,
                best_lap_ms=best_lap_ms,
            )
        )

    return entries
