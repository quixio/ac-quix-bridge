"""
Telemetry Comparison — FastAPI service for cross-run/lap telemetry visualization.

Queries Hive-partitioned Parquet data in QuixLake via SQL (DuckDB) and serves
an interactive Plotly.js UI for overlaying telemetry from different sessions/laps.
"""

import csv
import json
import os
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import re

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from quixlake import QuixLakeClient

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="Telemetry Comparison")

TABLE_NAME = os.getenv("TABLE_NAME", "ac_telemetry")
QUIXLAKE_URL = os.getenv("QUIXLAKE_URL")
QUIX_LAKE_TOKEN = os.getenv("QUIX_LAKE_TOKEN")
BLOB_VIDEO_PREFIX = os.getenv("BLOB_VIDEO_PREFIX", "ac_video")

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
CHANNELS_FILE = BASE_DIR / "channels.json"
TRACKS_CONFIG_FILE = BASE_DIR / "tracks_config.json"


def _get_blob_fs():
    """Get quixportal filesystem for blob storage. Returns None if unavailable.
    Used by the video sync endpoints to fetch MP4s + sidecar JSONs from S3."""
    try:
        from quixportal.storage import get_filesystem
        fs = get_filesystem()
        logger.info("Blob storage connected (prefix=%s)", BLOB_VIDEO_PREFIX)
        return fs
    except Exception as e:
        logger.warning("Blob storage not available — video sync will return 503: %s", e)
        return None


blob_fs = _get_blob_fs()

# Load channel metadata at startup
with open(CHANNELS_FILE) as f:
    _raw = json.load(f)
CHANNELS = {k: v for k, v in _raw.items() if not k.startswith("_")}

# Load tracks config at startup
with open(TRACKS_CONFIG_FILE) as f:
    TRACKS_CONFIG = {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def get_client() -> QuixLakeClient:
    return QuixLakeClient(base_url=QUIXLAKE_URL, token=QUIX_LAKE_TOKEN)


def sanitize_df(df):
    """Replace NaN/Inf with None for JSON serialization."""
    return df.where(df.notna(), None)


def _build_partition_filter(**kwargs) -> str:
    """Build a WHERE clause from partition column values.
    Skips empty strings. Uses CAST for session_id to handle
    DuckDB timestamp normalization vs Hive partition format."""
    clauses = []
    for col, val in kwargs.items():
        if val is None or val == "":
            continue
        if isinstance(val, int):
            clauses.append(f"{col} = {val}")
        elif col == "session_id":
            # Hive partitions store session_id as e.g. "2026-04-14T11:42:08.107Z"
            # but the frontend may send "2026-04-14 11:42:08.107000" (space, microseconds, no Z).
            # Use CAST to VARCHAR + LIKE prefix match to handle all format variations.
            # Strip trailing zeros and Z to get a common prefix for matching.
            prefix = val.replace('T', ' ').rstrip('Z').rstrip('0').rstrip('.')
            clauses.append(
                f"CAST(session_id AS VARCHAR) LIKE '{prefix}%'"
            )
        else:
            clauses.append(f"{col} = '{val}'")
    return ("WHERE " + " AND ".join(clauses)) if clauses else ""


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    """List available sessions with their metadata (partition fields)."""
    try:
        client = get_client()
        df = client.query(f"""
            SELECT
                environment,
                test_rig,
                experiment,
                driver,
                track,
                carModel,
                session_id,
                MIN(timestamp_ms) as first_ts,
                MAX(timestamp_ms) as last_ts,
                MAX(lap) as max_lap,
                COUNT(*) as total_samples
            FROM {TABLE_NAME}
            GROUP BY environment, test_rig, experiment, driver, track, carModel, session_id
            ORDER BY first_ts DESC
            LIMIT {limit}
        """)
        df = df.fillna("")
        return JSONResponse(content={"sessions": df.to_dict(orient="records")})
    except Exception as e:
        logger.exception("Failed to list sessions")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/laps")
async def list_laps(
    environment: str = "", test_rig: str = "", experiment: str = "",
    driver: str = "", track: str = "", carModel: str = "", session_id: str = "",
):
    """List laps for a given run (identified by all partition columns)."""
    try:
        client = get_client()
        where = _build_partition_filter(
            environment=environment, test_rig=test_rig, experiment=experiment,
            driver=driver, track=track, carModel=carModel, session_id=session_id,
        )
        df = client.query(f"""
            SELECT
                lap,
                ROUND(AVG(speedKmh), 1) as avg_speed,
                ROUND(MAX(speedKmh), 1) as max_speed,
                COUNT(*) as samples
            FROM {TABLE_NAME}
            {where}
            GROUP BY lap
            ORDER BY lap
        """)
        return JSONResponse(content={"laps": df.to_dict(orient="records")})
    except Exception as e:
        logger.exception("Failed to list laps")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/telemetry")
async def get_telemetry(
    lap: int,
    signals: str = "speedKmh,gas,brake,steerAngle",
    environment: str = "", test_rig: str = "", experiment: str = "",
    driver: str = "", track: str = "", carModel: str = "", session_id: str = "",
):
    """Get telemetry data for a specific run/lap, ordered by track position."""
    signal_list = [s.strip() for s in signals.split(",") if s.strip()]
    for s in signal_list:
        if not s.isidentifier():
            raise HTTPException(status_code=400, detail=f"Invalid signal name: {s}")

    columns = ", ".join(signal_list)
    where = _build_partition_filter(
        environment=environment, test_rig=test_rig, experiment=experiment,
        driver=driver, track=track, carModel=carModel, session_id=session_id,
        lap=lap,
    )
    try:
        client = get_client()
        df = client.query(f"""
            SELECT
                normalizedCarPosition,
                timestamp_ms,
                {columns}
            FROM {TABLE_NAME}
            {where}
            ORDER BY normalizedCarPosition
        """)
        df = sanitize_df(df)
        return JSONResponse(content={
            "session_id": session_id,
            "lap": lap,
            "signals": signal_list,
            "count": len(df),
            "data": df.to_dict(orient="list"),
        })
    except Exception as e:
        logger.exception("Failed to get telemetry")
        raise HTTPException(status_code=500, detail=str(e))


PARTITION_COLS = ["environment", "test_rig", "experiment", "driver", "track", "carModel", "session_id"]


@app.get("/api/partition-values")
async def partition_values(
    column: str,
    environment: str = "", test_rig: str = "", experiment: str = "",
    driver: str = "", track: str = "", carModel: str = "", session_id: str = "",
):
    """Return distinct values for a partition column, filtered by upstream selections."""
    if column not in PARTITION_COLS:
        raise HTTPException(status_code=400, detail=f"Invalid partition column: {column}")

    # Only filter by columns that come BEFORE the requested one in the hierarchy
    col_idx = PARTITION_COLS.index(column)
    upstream = {c: v for c, v in {
        "environment": environment, "test_rig": test_rig, "experiment": experiment,
        "driver": driver, "track": track, "carModel": carModel, "session_id": session_id,
    }.items() if PARTITION_COLS.index(c) < col_idx}

    where = _build_partition_filter(**upstream)
    try:
        client = get_client()
        select = column
        df = client.query(f"""
            SELECT DISTINCT {select}
            FROM {TABLE_NAME}
            {where}
            ORDER BY {column}
        """)
        df = df.fillna("")
        values = df[column].tolist()
        return JSONResponse(content={"values": values})
    except Exception as e:
        logger.exception("Failed to get partition values")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/channels")
async def list_channels():
    """Return channel metadata grouped by category."""
    return JSONResponse(content=CHANNELS)


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Track data
# ---------------------------------------------------------------------------

def _classify_radius(r_m: float) -> str:
    t = TRACKS_CONFIG["corner_thresholds"]
    if r_m < t["hairpin_max"]:
        return "hairpin"
    if r_m < t["tight_max"]:
        return "tight"
    if r_m < t["sweeper_max"]:
        return "sweeper"
    return "straight"


def _load_track_csv(rel_path: str) -> dict:
    csv_path = BASE_DIR / rel_path
    if not csv_path.exists():
        raise FileNotFoundError(f"Track file not found: {rel_path}")

    points = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                points.append({
                    "x": float(row["x"]),
                    "z": float(row["z"]),
                    "distance_m": float(row["distance_m"]),
                    "normalizedDistance": float(row["normalizedDistance"]),
                    "radius_m": float(row["radius_m"]),
                    "speed_kmh": float(row.get("speed_kmh", 0) or 0),
                    "gradient_pct": float(row.get("gradient_pct", 0) or 0),
                    "width_total_m": float(row.get("width_total_m", 0) or 0),
                    "severity": _classify_radius(float(row["radius_m"])),
                })
            except (KeyError, ValueError):
                continue

    # Group contiguous non-straight runs into labeled corners (T1..Tn)
    corners = []
    min_len_m = TRACKS_CONFIG.get("corner_labels", {}).get("min_length_m", 20)
    i = 0
    n = len(points)
    while i < n:
        sev = points[i]["severity"]
        if sev == "straight":
            i += 1
            continue
        j = i
        while j < n and points[j]["severity"] != "straight":
            j += 1
        # [i, j) is a corner run
        seg_len = points[j - 1]["distance_m"] - points[i]["distance_m"]
        if seg_len >= min_len_m:
            # Dominant severity = smallest min radius in the run
            min_r = min(points[k]["radius_m"] for k in range(i, j))
            corners.append({
                "index": len(corners) + 1,
                "label": f"T{len(corners) + 1}",
                "severity": _classify_radius(min_r),
                "start_norm": points[i]["normalizedDistance"],
                "end_norm": points[j - 1]["normalizedDistance"],
                "start_m": points[i]["distance_m"],
                "end_m": points[j - 1]["distance_m"],
                "min_radius_m": round(min_r, 1),
                "mid_x": points[(i + j - 1) // 2]["x"],
                "mid_z": points[(i + j - 1) // 2]["z"],
            })
        i = j

    return {
        "points": points,
        "corners": corners,
        "total_length_m": points[-1]["distance_m"] if points else 0,
    }


@app.get("/api/track")
async def get_track():
    """Return the default track: points + classified corners."""
    try:
        rel_path = TRACKS_CONFIG.get("default_track", "tracks/ks_nurburgring/layout_sprint_a.csv")
        data = _load_track_csv(rel_path)
        return JSONResponse(content={"track_file": rel_path, **data})
    except Exception as e:
        logger.exception("Failed to load track")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/track/config")
async def get_track_config():
    """Return the tracks config (thresholds, colors)."""
    return JSONResponse(content=TRACKS_CONFIG)


# ---------------------------------------------------------------------------
# Video sync (MP4 + sidecar JSON proxy from blob storage)
# ---------------------------------------------------------------------------

def _safe_session(session_id: str) -> str:
    """Convert telemetry session_id (with colons) to the storage form (hyphens).
    Idempotent — passing an already-safe id is a no-op."""
    return session_id.replace(":", "-")


def _session_blob_variants(session_id: str) -> list[str]:
    """Return possible blob-safe forms of a session_id.

    Handles format differences between Quix Cloud
    ('2026-04-14T11:42:08.107Z') and Quix Dev
    ('2026-04-14 11:42:08.1070000')."""
    safe = _safe_session(session_id)
    variants = [safe]
    # Cloud → Dev: T→space, strip Z, pad fractional seconds to 7 digits
    if "T" in safe and safe.endswith("Z"):
        alt = safe.replace("T", " ")[:-1]
        if "." in alt:
            base, frac = alt.rsplit(".", 1)
            alt = f"{base}.{frac.ljust(7, '0')}"
        if alt != safe:
            variants.append(alt)
    # Dev → Cloud: space→T, trim fractional to 3 digits, add Z
    if " " in safe and not safe.endswith("Z"):
        alt = safe.replace(" ", "T")
        if "." in alt:
            base, frac = alt.rsplit(".", 1)
            alt = f"{base}.{frac[:3]}Z"
        elif not alt.endswith("Z"):
            alt += "Z"
        if alt != safe:
            variants.append(alt)
    return variants


def _find_video_paths(session_id: str, lap: int) -> tuple[str, str] | None:
    """Find MP4 + sidecar blob paths for a session+lap, trying format variants.
    Returns (mp4_path, sidecar_path) or None if no video found."""
    if not blob_fs:
        return None
    for safe in _session_blob_variants(session_id):
        folder = f"{BLOB_VIDEO_PREFIX}/session_id={safe}"
        base = f"{safe}_lap{lap:03d}"
        mp4 = f"{folder}/{base}.mp4"
        try:
            blob_fs.invalidate_cache(folder)
            if blob_fs.exists(mp4):
                return mp4, f"{folder}/{base}.sync.json"
        except Exception:
            continue
    return None


@app.get("/api/video/{session_id}/{lap}")
async def get_video_meta(session_id: str, lap: int):
    """Return sidecar sync data + MP4 stream URL for a session+lap.

    Response shape:
      {
        "has_video": bool,
        "has_sync": bool,
        "sync": {...} | None,
        "mp4_url": str | None,
        "message": str | None
      }"""
    if not blob_fs:
        raise HTTPException(503, "Blob storage not connected")

    result = _find_video_paths(session_id, lap)
    if not result:
        return JSONResponse({
            "has_video": False,
            "has_sync": False,
            "sync": None,
            "mp4_url": None,
            "message": f"No video recorded for session {session_id} lap {lap}",
        })

    mp4_path, sidecar_path = result
    sync = None
    try:
        sidecar_bytes = blob_fs.cat(sidecar_path)
        sync = json.loads(sidecar_bytes)
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to read sidecar JSON: %s", sidecar_path)

    return JSONResponse({
        "has_video": True,
        "has_sync": sync is not None,
        "sync": sync,
        "mp4_url": f"/api/video/{session_id}/{lap}/mp4",
        "message": None if sync else "Video recorded but sync metadata not available",
    })


_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


@app.get("/api/video/{session_id}/{lap}/mp4")
async def stream_video(
    session_id: str,
    lap: int,
    range: str | None = Header(default=None),
):
    """Serve MP4 bytes from blob storage with HTTP Range support.

    Range support is required for the <video> element to seek into
    unbuffered regions — without it, scrubbing-while-paused doesn't work
    because the browser can only see whatever it has linearly downloaded.
    """
    if not blob_fs:
        raise HTTPException(503, "Blob storage not connected")

    # Try session_id format variants to find the actual blob path
    mp4_path = None
    total = 0
    for safe in _session_blob_variants(session_id):
        folder = f"{BLOB_VIDEO_PREFIX}/session_id={safe}"
        base = f"{safe}_lap{lap:03d}"
        candidate = f"{folder}/{base}.mp4"
        try:
            info = blob_fs.info(candidate)
            mp4_path = candidate
            total = int(info.get("size", 0))
            break
        except FileNotFoundError:
            continue
        except Exception:
            logger.exception("Failed to stat MP4: %s", candidate)
            continue

    if not mp4_path:
        raise HTTPException(404, f"Video not found: session={session_id} lap={lap}")

    common = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=300",
    }

    if not range:
        try:
            data = blob_fs.cat(mp4_path)
        except FileNotFoundError:
            raise HTTPException(404, f"Video not found: session={session_id} lap={lap}")
        except Exception:
            logger.exception("Failed to fetch MP4 from blob: %s", mp4_path)
            raise HTTPException(500, "Failed to fetch video")
        return Response(
            content=data,
            media_type="video/mp4",
            headers={**common, "Content-Length": str(len(data))},
        )

    m = _RANGE_RE.match(range.strip())
    if not m:
        raise HTTPException(416, f"Invalid Range header: {range}")
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else total - 1
    end = min(end, total - 1)
    if start > end or start >= total:
        return Response(
            status_code=416,
            headers={**common, "Content-Range": f"bytes */{total}"},
        )
    length = end - start + 1

    try:
        with blob_fs.open(mp4_path, "rb") as fh:
            fh.seek(start)
            chunk = fh.read(length)
    except Exception:
        logger.exception(
            "Failed to read range %d-%d from %s", start, end, mp4_path
        )
        raise HTTPException(500, "Failed to read video range")

    return Response(
        content=chunk,
        status_code=206,
        media_type="video/mp4",
        headers={
            **common,
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{total}",
        },
    )


# ---------------------------------------------------------------------------
# Static files & SPA
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
