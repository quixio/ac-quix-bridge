"""
Leaderboard routes — best-lap aggregation served from the Quix Lake.

The endpoint runs a single SQL aggregation against the lake's `ac_telemetry`
table (via the `{measurements_url}/api/query` SQL-over-HTTP endpoint used by
`integrations.download_test_data`) and returns the full per-(track, car,
experiment, driver) best-lap matrix. The frontend fetches once on tab mount
and derives the Track/Car/Experiment dropdowns + filters client-side.

Driver names are rewritten on the server to the display-case form stored in
the Mongo `drivers.name` collection. The lake partitions `driver` in
lowercase (see `tests.py:128` — `test_data.driver.lower()`), so a Mongo
lookup is the canonical way to recover proper casing. Unmatched drivers
keep their raw lowercase value rather than being dropped.
"""

import csv
import logging
import os
import unicodedata
from io import StringIO
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database

from ..auth import read_permission
from ..models import BestLapEntry
from ..mongo import get_mongo
from ..settings import get_settings
from .integrations import get_measurements_url_base
from .settings import get_effective_integration_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


# Dev-mode hardcoded fallback — same literal as `test-manager-backend/app.yaml`
# `MEASUREMENTS_URL` defaultValue. Lets the leaderboard work locally without
# requiring a user to configure `measurements_deployment` via Settings UI.
# Why: the Settings flow requires picking a deployment reference from the Quix
# portal, which is heavyweight for local development. The URL already lives
# in the repo; we reuse it.
_FALLBACK_MEASUREMENTS_URL = "https://query-ui-quixers-testrigdemodatawarehouse-prod.az-france-0.app.quix.io"


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
# track, carModel, session_id, lap) — see quix.yaml:127. `iLastTime` is AC's
# "last completed lap time in ms" from the Graphics struct — constant within
# a lap partition once the lap is complete. `lap > 1` skips the out-lap
# (lap = completedLaps + 1, so completedLaps = 0 → lap 1 = out-lap).
#
# Aggregation strategy: MAX(iLastTime) per lap-partition, then MIN across
# sessions for the per-driver best. HAVING filters rows where the lap never
# reported a positive last-time (stale / zero-init / crashed-out rows).
_BEST_LAPS_SQL = """
WITH per_lap AS (
  SELECT
    track,
    carModel,
    experiment,
    driver,
    session_id,
    lap,
    MAX(iLastTime) AS lap_time_ms
  FROM ac_telemetry
  WHERE lap > 1
  GROUP BY track, carModel, experiment, driver, session_id, lap
  HAVING MAX(iLastTime) > 0
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


def _parse_csv_rows(csv_text: str) -> list[dict[str, str]]:
    """Parse a CSV response body into a list of row dicts. Empty body → []."""
    if not csv_text or not csv_text.strip():
        return []
    reader = csv.DictReader(StringIO(csv_text))
    return [row for row in reader]


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
    integration_settings = get_effective_integration_settings()

    measurements_url = (
        get_measurements_url_base(integration_settings)
        or settings.measurements_url
        or _FALLBACK_MEASUREMENTS_URL
    )
    if measurements_url is _FALLBACK_MEASUREMENTS_URL:
        logger.info(
            "No measurements deployment configured; using hardcoded fallback URL."
        )

    # Strip a single trailing slash so the `/api/query` suffix never doubles up.
    api_url = f"{measurements_url.rstrip('/')}/api/query"

    try:
        async with httpx.AsyncClient() as client:
            logger.info("Querying Quix Lake API for best laps: %s", api_url)
            response = await client.post(
                api_url,
                content=_BEST_LAPS_SQL,
                headers={
                    "Authorization": f"Bearer {settings.sdk_token}",
                    "Content-Type": "text/plain",
                },
                timeout=30.0,
            )

            if not response.is_success:
                logger.error(
                    "Quix Lake Query API error: status=%s body=%s",
                    response.status_code,
                    response.text[:500],
                )
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"Query API error: {response.status_code} - {response.text}",
                )

            rows = _parse_csv_rows(response.text)
            logger.info("Best-laps aggregation returned %d rows", len(rows))

    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Query API timeout")
    except httpx.HTTPError as e:
        logger.error("HTTP error querying Quix Lake API: %s", e)
        raise HTTPException(status_code=500, detail=f"Query API error: {str(e)}")

    # Build the driver display-case lookup from Mongo. One call per
    # aggregation — trivial.
    driver_name_lookup = _build_driver_name_lookup(mongo)

    entries: list[BestLapEntry] = []
    for row in rows:
        # Empty or malformed cells → skip the row; the SQL is static, so
        # this should only happen if the upstream schema drifts.
        raw_best = row.get("best_lap_ms")
        if raw_best in (None, ""):
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
