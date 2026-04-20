"""Track data loading and corner classification.

Serves the static track geometry + corner metadata from a CSV. Orthogonal to
telemetry queries — the track file is bundled with the service, no lake hit.
Exposed via an APIRouter that main.py mounts.
"""

from __future__ import annotations

import csv
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

import config

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


@router.get("/api/track")
async def get_track():
    """Return the default track: points + classified corners."""
    try:
        rel_path = config.DEFAULT_TRACK_CSV
        data = _load_track_csv(rel_path)
        return JSONResponse(content={"track_file": rel_path, **data})
    except Exception as e:
        logger.exception("Failed to load track")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/track/config")
async def get_track_config():
    """Return rendering constants (thresholds, colors)."""
    return JSONResponse(
        content={
            "corner_thresholds": config.CORNER_THRESHOLDS,
            "colors": config.TRACK_COLORS,
        }
    )
