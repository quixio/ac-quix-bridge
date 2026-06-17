"""Track data loading and corner classification.

Serves static track geometry + corner metadata. Two sources, in precedence:

  1. MongoDB `track_layouts` (track-importer output), selected by the
     frontend's `track` (+ optional `layout`) dropdown. Read-only.
  2. The bundled `DEFAULT_TRACK_CSV` fallback (offline / Mongo-down / no
     `track` param selected yet). Never deleted — the `tracks/` dir and
     `_load_track_csv()` are retained.

Both sources are transformed into ONE response shape (the `/api/track`
contract) so the frontend consumers (track-map.js, charts.js, sync.js) never
have to know where the geometry came from. Orthogonal to telemetry queries —
no lake hit. Exposed via an APIRouter that main.py mounts.
"""

from __future__ import annotations

import bisect
import csv
import logging
import math
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

import config
import mongo
import mongo_settings

logger = logging.getLogger(__name__)
router = APIRouter()


def _classify_radius(r_m: float) -> str:
    if r_m < config.CORNER_THRESHOLDS["hairpin_max"]:
        return "hairpin"
    if r_m < config.CORNER_THRESHOLDS["tight_max"]:
        return "tight"
    if r_m < config.CORNER_THRESHOLDS["sweeper_max"]:
        return "sweeper"
    return "straight"


def _load_track_csv(rel_path: str) -> dict:
    csv_path = config.BASE_DIR / rel_path
    if not csv_path.exists():
        raise FileNotFoundError(f"Track file not found: {rel_path}")

    points = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                points.append(
                    {
                        "x": float(row["x"]),
                        "z": float(row["z"]),
                        "distance_m": float(row["distance_m"]),
                        "normalizedDistance": float(row["normalizedDistance"]),
                        "radius_m": float(row["radius_m"]),
                        "speed_kmh": float(row.get("speed_kmh", 0) or 0),
                        "gradient_pct": float(row.get("gradient_pct", 0) or 0),
                        "width_total_m": float(row.get("width_total_m", 0) or 0),
                        "severity": _classify_radius(float(row["radius_m"])),
                        "corner_designation": row.get("corner_designation", ""),
                        "corner_name": row.get("corner_name", ""),
                        "corner_type": row.get("corner_type", ""),
                        "corner_direction": row.get("corner_direction", ""),
                    }
                )
            except (KeyError, ValueError):
                continue

    # Build corners from CSV columns (corner_designation, corner_name, etc.)
    # Group contiguous rows with the same non-empty corner_designation.
    corners = []
    i = 0
    n = len(points)
    while i < n:
        desig = points[i]["corner_designation"]
        if not desig:
            i += 1
            continue
        j = i
        while j < n and points[j]["corner_designation"] == desig:
            j += 1
        # [i, j) is a corner run with the same designation
        min_r = min(points[k]["radius_m"] for k in range(i, j))
        corners.append(
            {
                "index": len(corners) + 1,
                "label": desig,
                "name": points[i]["corner_name"],
                "type": points[i]["corner_type"],
                "direction": points[i]["corner_direction"],
                "severity": _classify_radius(min_r),
                "start_norm": points[i]["normalizedDistance"],
                "end_norm": points[j - 1]["normalizedDistance"],
                "start_m": points[i]["distance_m"],
                "end_m": points[j - 1]["distance_m"],
                "min_radius_m": round(min_r, 1),
                "mid_x": points[(i + j - 1) // 2]["x"],
                "mid_z": points[(i + j - 1) // 2]["z"],
            }
        )
        i = j

    return {
        "points": points,
        "corners": corners,
        "total_length_m": points[-1]["distance_m"] if points else 0,
    }


# ---------------------------------------------------------------------------
# Mongo source
# ---------------------------------------------------------------------------


def _stride_sample_points(raw_points: list[dict], corners: list[dict], cap: int) -> list[dict]:
    """Uniformly stride-sample `raw_points` to at most `cap`, keeping the first
    and last point and every corner-boundary point so corner ranges and the
    closed polyline survive the downsample.

    Mongo points are ordered by `distance_m`; corner boundaries are matched by
    nearest distance index. Returns the points in original order.
    """
    n = len(raw_points)
    if cap <= 0 or n <= cap:
        return raw_points

    distances = [p.get("distance_m", 0.0) or 0.0 for p in raw_points]

    def _nearest_idx(target_m: float) -> int:
        pos = bisect.bisect_left(distances, target_m)
        if pos <= 0:
            return 0
        if pos >= n:
            return n - 1
        before = distances[pos - 1]
        after = distances[pos]
        return pos if (after - target_m) < (target_m - before) else pos - 1

    keep: set[int] = {0, n - 1}
    step = math.ceil(n / cap)
    keep.update(range(0, n, step))
    for c in corners:
        keep.add(_nearest_idx(c.get("distance_start_m", 0.0) or 0.0))
        keep.add(_nearest_idx(c.get("distance_end_m", 0.0) or 0.0))

    return [raw_points[i] for i in sorted(keep)]


def _build_corner_lookup(corners: list[dict]) -> list[tuple[float, float, dict]]:
    """Pre-compute (start_m, end_m, corner) tuples for per-point stamping,
    sorted by start_m for a quick range scan."""
    ranges = [
        (
            float(c.get("distance_start_m", 0.0) or 0.0),
            float(c.get("distance_end_m", 0.0) or 0.0),
            c,
        )
        for c in corners
    ]
    ranges.sort(key=lambda r: r[0])
    return ranges


def _corner_for_distance(ranges: list[tuple[float, float, dict]], d_m: float) -> dict | None:
    """Return the Mongo corner whose [start_m, end_m] contains d_m, else None."""
    for start_m, end_m, corner in ranges:
        if start_m <= d_m <= end_m:
            return corner
        if start_m > d_m:
            break
    return None


def _transform_mongo_doc(doc: dict) -> dict:
    """Transform a `track_layouts` document into the `/api/track` contract.

    Mongo points carry NO `normalizedDistance` and NO `corner_*` fields — those
    are derived here. Corners are built DIRECTLY from the authoritative Mongo
    `corners[]` array (not re-grouped from per-point fields); per-point corner
    fields are stamped by distance-range purely for downstream parity with the
    CSV path.
    """
    length_m = float(doc.get("length_m", 0.0) or 0.0)
    raw_corners = doc.get("corners", []) or []
    raw_points = doc.get("points", []) or []

    # Bound payload size before the per-point transform.
    sampled = _stride_sample_points(raw_points, raw_corners, mongo_settings.TRACK_MAX_POINTS)

    ranges = _build_corner_lookup(raw_corners)

    points: list[dict] = []
    for rp in sampled:
        d_m = float(rp.get("distance_m", 0.0) or 0.0)
        radius_m = float(rp.get("radius_m", 0.0) or 0.0)
        corner = _corner_for_distance(ranges, d_m)
        if corner is not None:
            cid = corner.get("id")
            desig = f"T{cid}"
            cname = f"T{cid}"
            ctype = corner.get("type", "") or ""
            cdir = corner.get("direction", "") or ""
        else:
            desig = cname = ctype = cdir = ""
        points.append(
            {
                "x": float(rp.get("x", 0.0) or 0.0),
                "z": float(rp.get("z", 0.0) or 0.0),
                "distance_m": d_m,
                "normalizedDistance": (d_m / length_m) if length_m else 0.0,
                "radius_m": radius_m,
                "speed_kmh": float(rp.get("speed_kmh", 0) or 0),
                "gradient_pct": float(rp.get("gradient_pct", 0) or 0),
                "width_total_m": float(rp.get("width_total_m", 0) or 0),
                "severity": _classify_radius(radius_m),
                "corner_designation": desig,
                "corner_name": cname,
                "corner_type": ctype,
                "corner_direction": cdir,
            }
        )

    # Mid-point lookup uses the (sampled) points so mid_x/mid_z reference a
    # point that actually exists in the returned array.
    sampled_dist = [p["distance_m"] for p in points]

    def _mid_xz(start_m: float, end_m: float) -> tuple[float, float]:
        if not points:
            return 0.0, 0.0
        mid_m = (start_m + end_m) / 2
        pos = bisect.bisect_left(sampled_dist, mid_m)
        if pos <= 0:
            idx = 0
        elif pos >= len(points):
            idx = len(points) - 1
        else:
            before = sampled_dist[pos - 1]
            after = sampled_dist[pos]
            idx = pos if (after - mid_m) < (mid_m - before) else pos - 1
        return points[idx]["x"], points[idx]["z"]

    corners: list[dict] = []
    for c in raw_corners:
        cid = c.get("id")
        start_m = float(c.get("distance_start_m", 0.0) or 0.0)
        end_m = float(c.get("distance_end_m", 0.0) or 0.0)
        min_r = float(c.get("min_radius_m", 0.0) or 0.0)
        mid_x, mid_z = _mid_xz(start_m, end_m)
        corners.append(
            {
                "index": cid,
                "label": f"T{cid}",
                "name": f"T{cid}",
                "type": c.get("type", "") or "",
                "direction": c.get("direction", "") or "",
                "severity": _classify_radius(min_r),
                "start_norm": (start_m / length_m) if length_m else 0.0,
                "end_norm": (end_m / length_m) if length_m else 0.0,
                "start_m": start_m,
                "end_m": end_m,
                "min_radius_m": round(min_r, 1),
                "mid_x": mid_x,
                "mid_z": mid_z,
            }
        )

    return {
        "points": points,
        "corners": corners,
        "total_length_m": length_m,
    }


def _ci_exact(value: str) -> dict:
    """Anchored, case-insensitive equality match for a Mongo string field.

    The lake supplies `track` in its own casing (e.g. `Spa`) while Mongo
    `track_layouts` keys are lowercase AC folder names (`spa`). An anchored
    `$regex` with the `i` option matches across that casing gap without
    assuming the lake value is a pure lowercase of the folder. The value flows
    from a query param, so it is `re.escape`-d before interpolation.
    """
    return {"$regex": f"^{re.escape(value)}$", "$options": "i"}


def _resolve_mongo_doc(track: str, layout: str) -> dict | None:
    """Read-only resolution of a track_layouts doc.

    - `layout` present → match `_id == "<track>/<layout>"` case-insensitively
      (both `_id` halves are lowercase in Mongo; the lake casing may differ).
    - `layout` absent → query `{track}` case-insensitively; if exactly one doc
      use it, if multiple pick the deterministic first by sorted `layout`.
    Returns the doc (or None if no match). Raises PyMongoError on unreachable
    Mongo (caller falls back to CSV).
    """
    db = mongo.get_mongo()
    coll = db[mongo_settings.TRACK_LAYOUTS_COLLECTION]
    if layout:
        return coll.find_one({"_id": _ci_exact(f"{track}/{layout}")})
    cursor = coll.find({"track": _ci_exact(track)}).sort([("layout", 1)]).limit(1)
    docs = list(cursor)
    return docs[0] if docs else None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/api/track")
async def get_track(track: str = "", layout: str = ""):
    """Return track geometry + classified corners.

    Precedence: Mongo (when `track` given AND reachable AND doc found) →
    bundled `DEFAULT_TRACK_CSV` → HTTP 500. Response shape is identical
    regardless of source; `track_file` carries provenance (`mongo:<_id>` vs the
    CSV path).
    """
    if track:
        try:
            doc = _resolve_mongo_doc(track, layout)
            if doc is not None:
                data = _transform_mongo_doc(doc)
                return JSONResponse(content={"track_file": f"mongo:{doc.get('_id')}", **data})
            logger.warning(
                "No track_layouts doc for track=%r layout=%r — falling back to CSV",
                track,
                layout,
            )
        except (PyMongoError, RuntimeError) as e:
            logger.warning(
                "Mongo lookup failed for track=%r layout=%r (%s) — falling back to CSV",
                track,
                layout,
                e,
            )

    # CSV fallback (no track param, Mongo down, or no matching doc).
    try:
        rel_path = config.DEFAULT_TRACK_CSV
        data = _load_track_csv(rel_path)
        return JSONResponse(content={"track_file": rel_path, **data})
    except Exception as e:
        logger.exception("Failed to load track (CSV fallback)")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/track/layouts")
async def get_track_layouts(track: str = ""):
    """List available Mongo layouts for a track (light projection, no geometry).

    Returns `{"track": ..., "layouts": [{layout, _id, length_m, n_corners}]}`
    sorted by `layout`. Returns 200 with an empty list on Mongo-unreachable or
    no docs (never 500) so the frontend simply hides the LAYOUT dropdown and
    uses the CSV fallback.
    """
    layouts: list[dict] = []
    if track:
        try:
            db = mongo.get_mongo()
            coll = db[mongo_settings.TRACK_LAYOUTS_COLLECTION]
            cursor = coll.find(
                {"track": _ci_exact(track)},
                {"layout": 1, "length_m": 1, "n_corners": 1},
            ).sort([("layout", 1)])
            layouts = [
                {
                    "layout": doc.get("layout"),
                    "_id": doc.get("_id"),
                    "length_m": doc.get("length_m"),
                    "n_corners": doc.get("n_corners"),
                }
                for doc in cursor
            ]
        except (PyMongoError, RuntimeError) as e:
            logger.warning("Mongo layouts lookup failed for track=%r (%s)", track, e)
            layouts = []
    return JSONResponse(content={"track": track, "layouts": layouts})


@router.get("/api/track/config")
async def get_track_config():
    """Return rendering constants (thresholds, colors)."""
    return JSONResponse(
        content={
            "corner_thresholds": config.CORNER_THRESHOLDS,
            "colors": config.TRACK_COLORS,
        }
    )
