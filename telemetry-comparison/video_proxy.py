"""Video sync — MP4 + sidecar JSON proxy from blob storage.

In Quix Cloud the container is auto-injected with a JSON blob storage config
(`Quix__BlobStorage__Connection__Json`) when the deployment is linked to a
blob connection (see `blobStorage: bind: true` in quix.yaml). Locally that
env var is absent, `get_filesystem()` raises, and video endpoints return 503.
Telemetry endpoints are unaffected.
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, Response

import config

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_blob_fs():
    """Get quixportal filesystem for blob storage. Returns None if unavailable.
    Used by the video sync endpoints to fetch MP4s + sidecar JSONs from S3."""
    try:
        from quixportal.storage import get_filesystem

        fs = get_filesystem()
        logger.info("Blob storage connected (prefix=%s)", config.BLOB_VIDEO_PREFIX)
        return fs
    except Exception as e:
        logger.warning("Blob storage not available — video sync will return 503: %s", e)
        return None


blob_fs = _get_blob_fs()


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
        folder = f"{config.BLOB_VIDEO_PREFIX}/session_id={safe}"
        base = f"{safe}_lap{lap:03d}"
        mp4 = f"{folder}/{base}.mp4"
        try:
            blob_fs.invalidate_cache(folder)
            if blob_fs.exists(mp4):
                return mp4, f"{folder}/{base}.sync.json"
        except Exception:
            continue
    return None


_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


@router.get("/api/video/{session_id}/{lap}")
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
        return JSONResponse(
            {
                "has_video": False,
                "has_sync": False,
                "sync": None,
                "mp4_url": None,
                "message": f"No video recorded for session {session_id} lap {lap}",
            }
        )

    mp4_path, sidecar_path = result
    sync = None
    try:
        sidecar_bytes = blob_fs.cat(sidecar_path)
        sync = json.loads(sidecar_bytes)
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to read sidecar JSON: %s", sidecar_path)

    return JSONResponse(
        {
            "has_video": True,
            "has_sync": sync is not None,
            "sync": sync,
            "mp4_url": f"/api/video/{session_id}/{lap}/mp4",
            "message": None if sync else "Video recorded but sync metadata not available",
        }
    )


@router.get("/api/video/{session_id}/{lap}/mp4")
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
        folder = f"{config.BLOB_VIDEO_PREFIX}/session_id={safe}"
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
        except FileNotFoundError as e:
            raise HTTPException(404, f"Video not found: session={session_id} lap={lap}") from e
        except Exception as e:
            logger.exception("Failed to fetch MP4 from blob: %s", mp4_path)
            raise HTTPException(500, "Failed to fetch video") from e
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
    except Exception as e:
        logger.exception("Failed to read range %d-%d from %s", start, end, mp4_path)
        raise HTTPException(500, "Failed to read video range") from e

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
