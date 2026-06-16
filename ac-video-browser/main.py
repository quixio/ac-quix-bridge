"""
AC Video Browser — browse and download recorded session MP4s from blob storage.

Lists sessions from S3, shows per-lap MP4 files, and serves them for download.
"""

import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BLOB_PREFIX = os.environ.get("BLOB_VIDEO_PREFIX", "ac_video")
STATIC_DIR = Path(__file__).parent / "static"


def _get_blob_fs():
    """Build the blob filesystem. Prefer parsing
    Quix__BlobStorage__Connection__Json directly so we can pass
    client_kwargs={"verify": False} for MinIO endpoints fronted by a
    self-signed cert chain (quixportal 2.0.1 has no knob for this).
    Falls back to quixportal.get_filesystem() when the JSON isn't set
    or the direct build fails — that preserves the working path for the
    older AWS S3 / GCP-S3 deployments with valid certs."""
    raw = os.environ.get("Quix__BlobStorage__Connection__Json", "").strip()
    if raw:
        try:
            cfg = json.loads(raw)
            s3 = cfg.get("S3Compatible") or cfg.get("s3_compatible") or {}
            bucket = s3.get("BucketName") or s3.get("bucket_name")
            endpoint = s3.get("ServiceUrl") or s3.get("service_url")
            access = s3.get("AccessKeyId") or s3.get("access_key_id")
            secret = s3.get("SecretAccessKey") or s3.get("secret_access_key")
            if all([bucket, endpoint, access, secret]):
                import fsspec
                inner = fsspec.filesystem(
                    "s3",
                    key=access,
                    secret=secret,
                    endpoint_url=endpoint,
                    use_ssl=endpoint.startswith("https://"),
                    client_kwargs={"verify": False},
                )
                inner.ls(f"{bucket}/", refresh=True)
                fs = fsspec.filesystem("dir", fs=inner, path=bucket)
                logger.info(
                    "Blob storage connected (s3://%s @ %s, SSL verify off)",
                    bucket, endpoint,
                )
                return fs
        except Exception as e:
            logger.warning(
                "Direct s3fs build failed (%s) — falling back to quixportal",
                e,
            )
    try:
        from quixportal.storage import get_filesystem
        fs = get_filesystem()
        logger.info("Blob storage connected (via quixportal)")
        return fs
    except Exception as e:
        logger.error("Blob storage not available: %s", e)
        return None


blob_fs = _get_blob_fs()
api = FastAPI(title="AC Video Browser")


def _invalidate(path: str) -> None:
    """Invalidate the s3fs dircache for ``path``.

    ``blob_fs`` is an fsspec ``DirFileSystem`` wrapping an inner ``S3FileSystem``;
    ``DirFileSystem.invalidate_cache`` doesn't apply its bucket prefix, so calls
    reach the inner FS with the unprefixed path while ``dircache`` is keyed
    ``<bucket>/<path>`` — leaving the real entry stuck. Pop on the inner FS using
    the prefixed key.
    """
    inner = getattr(blob_fs, "fs", blob_fs)
    bucket_path = getattr(blob_fs, "path", "")
    key = f"{bucket_path}/{path}" if bucket_path else path
    inner.invalidate_cache(key)


@api.get("/api/sessions")
def list_sessions():
    """List all session IDs in blob storage."""
    if not blob_fs:
        raise HTTPException(503, "Blob storage not connected")
    try:
        _invalidate(BLOB_PREFIX)
        entries = blob_fs.ls(BLOB_PREFIX, detail=False, refresh=True)
        sessions = []
        for entry in entries:
            path = entry if isinstance(entry, str) else entry.get("name", "")
            name = path.rsplit("/", 1)[-1]
            if name.startswith("session_id="):
                session_id = name.replace("session_id=", "")
                sessions.append(session_id)
        sessions.sort(reverse=True)
        return {"sessions": sessions}
    except FileNotFoundError:
        return {"sessions": []}


@api.get("/api/sessions/{session_id}/files")
def list_files(session_id: str):
    """List MP4 files for a session."""
    if not blob_fs:
        raise HTTPException(503, "Blob storage not connected")
    safe_id = session_id.replace(":", "-")
    prefix = f"{BLOB_PREFIX}/session_id={safe_id}"
    try:
        _invalidate(prefix)
        entries = blob_fs.ls(prefix, detail=True, refresh=True)
        files = []
        for entry in entries:
            name = entry["name"].rsplit("/", 1)[-1]
            if name.endswith(".mp4"):
                files.append({
                    "name": name,
                    "size_kb": round(entry.get("size", 0) / 1024),
                    "download_url": f"/api/sessions/{session_id}/files/{name}",
                })
        files.sort(key=lambda f: f["name"])
        return {"session_id": session_id, "files": files}
    except FileNotFoundError:
        raise HTTPException(404, f"Session {session_id} not found")


@api.get("/api/sessions/{session_id}/files/{filename}")
def download_file(session_id: str, filename: str):
    """Download an MP4 file from blob storage."""
    if not blob_fs:
        raise HTTPException(503, "Blob storage not connected")
    if not filename.endswith(".mp4"):
        raise HTTPException(400, "Only MP4 files can be downloaded")
    safe_id = session_id.replace(":", "-")
    blob_path = f"{BLOB_PREFIX}/session_id={safe_id}/{filename}"
    try:
        data = blob_fs.cat(blob_path)
        return StreamingResponse(
            iter([data]),
            media_type="video/mp4",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except FileNotFoundError:
        raise HTTPException(404, f"File not found: {filename}")


api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@api.get("/{full_path:path}")
def root(full_path: str = ""):
    return FileResponse(str(STATIC_DIR / "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(api, host="0.0.0.0", port=80)
