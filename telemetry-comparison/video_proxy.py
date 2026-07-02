"""Video sync — MP4 + sidecar JSON proxy from blob storage.

In Quix Cloud the container is auto-injected with a JSON blob storage config
(`Quix__BlobStorage__Connection__Json`) when the deployment is linked to a
blob connection (see `blobStorage: bind: true` in quix.yaml). Locally that
env var is absent, `get_filesystem()` raises, and video endpoints return 503.
Telemetry endpoints are unaffected.

Connection is established lazily on the first request, not at import — a cold
Quix Cloud environment start can bring the pod up before MinIO/blob is
reachable, so a connect attempt at import would fail permanently and every
video request would 503 until a manual restart. Instead `get_blob_fs()`
retries on later requests: a successful connect is cached for the life of the
process; a failure is retried at most once per `_RETRY_COOLDOWN_S` window
(within that window the accessor returns None without touching the network,
so request latency stays bounded when blob is genuinely down). Locally, where
the env var is absent, this just retries cheaply and keeps returning None
(video endpoints 503, telemetry unaffected). The accessor is thread-safe:
endpoints run in the event loop but the sprite slow paths run under
`asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse, Response

import config

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_blob_fs():
    """Get filesystem for blob storage. Returns None if unavailable.

    Prefer parsing Quix__BlobStorage__Connection__Json directly so we can
    pass client_kwargs={"verify": False} for MinIO endpoints fronted by a
    self-signed cert chain (quixportal 2.0.1 has no knob for this).
    Falls back to quixportal.get_filesystem() when the JSON isn't set or
    the direct build fails — preserves the working path for the older
    AWS S3 / GCP-S3 deployments with valid certs."""
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
                    "Blob storage connected (s3://%s @ %s, SSL verify off, prefix=%s)",
                    bucket, endpoint, config.BLOB_VIDEO_PREFIX,
                )
                return fs
        except Exception as e:
            logger.warning(
                "Direct s3fs build failed (%s) — falling back to quixportal", e,
            )
    try:
        from quixportal.storage import get_filesystem

        fs = get_filesystem()
        logger.info(
            "Blob storage connected via quixportal (prefix=%s)",
            config.BLOB_VIDEO_PREFIX,
        )
        return fs
    except Exception as e:
        logger.warning("Blob storage not available — video sync will return 503: %s", e)
        return None


# Lazy, cached, thread-safe accessor for the blob filesystem. Replaces a
# one-shot import-time connect so a cold-environment start (blob not yet
# reachable) is retried on later requests instead of 503-ing forever.
_RETRY_COOLDOWN_S = 10.0
_blob_fs = None
_last_attempt = 0.0
_blob_fs_lock = threading.Lock()


def get_blob_fs():
    """Return the blob filesystem, connecting lazily with a retry cooldown.

    A successful connect is cached permanently. On failure, at most one
    reconnect is attempted per `_RETRY_COOLDOWN_S` window; within the window
    None is returned without any network I/O so request latency stays bounded
    when blob storage is genuinely down. Thread-safe — the sprite slow paths
    call this from `asyncio.to_thread`.
    """
    global _blob_fs, _last_attempt
    if _blob_fs is not None:
        return _blob_fs
    with _blob_fs_lock:
        # Re-check inside the lock: another thread may have just connected.
        if _blob_fs is not None:
            return _blob_fs
        if time.monotonic() - _last_attempt < _RETRY_COOLDOWN_S:
            return None
        _last_attempt = time.monotonic()
        # _get_blob_fs() logs its own success (logger.info) / failure
        # (logger.warning); the cooldown naturally throttles the warning.
        _blob_fs = _get_blob_fs()
        return _blob_fs


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
    fs = get_blob_fs()
    if not fs:
        return None
    for safe in _session_blob_variants(session_id):
        folder = f"{config.BLOB_VIDEO_PREFIX}/session_id={safe}"
        base = f"{safe}_lap{lap:03d}"
        mp4 = f"{folder}/{base}.mp4"
        try:
            fs.invalidate_cache(folder)
            if fs.exists(mp4):
                return mp4, f"{folder}/{base}.sync.json"
        except Exception:
            continue
    return None


_RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


# ---------------------------------------------------------------------------
# Sprite-sheet thumbnails for the marker-drag frame preview.
# Spec: dev-planning/marker-drag-frame-preview/spec.md §5.2 (lazy proxy path).
# Eager sprites are produced by the recorder (ac_video_streaming/video_recorder.py)
# and uploaded as `<base>.thumbs.jpg` next to the MP4. When that file is
# missing — laps recorded before the recorder change — we download the MP4,
# run ffmpeg locally, upload the sprite, fold the `thumbs` block into the
# `.sync.json` sidecar, and stream the bytes back. Per-key asyncio.Lock keeps
# concurrent first-time requests for the same lap from running ffmpeg twice.
# ---------------------------------------------------------------------------

# Tile metadata mirrors the recorder. Tile width is computed per-MP4 from the
# real aspect ratio; height is fixed at 90 px.
_SPRITE_TILES = 100
_SPRITE_COLS = 10
_SPRITE_ROWS = 10
_SPRITE_TILE_H = 90

# Per-(session_id, lap) lock. Module-level lifetime; entries are popped after
# the slow path completes so the registry doesn't grow unbounded over time.
_sprite_locks: dict[tuple[str, int], asyncio.Lock] = {}


def _sprite_blob_paths(safe_session: str, lap: int) -> tuple[str, str, str]:
    """Return (folder, sprite_blob, sidecar_blob) for a given safe_session+lap."""
    folder = f"{config.BLOB_VIDEO_PREFIX}/session_id={safe_session}"
    base = f"{safe_session}_lap{lap:03d}"
    return folder, f"{folder}/{base}.thumbs.jpg", f"{folder}/{base}.sync.json"


def _find_sprite_paths(session_id: str, lap: int) -> tuple[str, str, str, str] | None:
    """Locate the MP4 + canonical sprite/sidecar blob paths for this lap.

    Returns (mp4_path, sprite_path, sidecar_path, safe_session) or None if
    no MP4 exists for any session_id format variant.
    """
    fs = get_blob_fs()
    if not fs:
        return None
    for safe in _session_blob_variants(session_id):
        folder, sprite_path, sidecar_path = _sprite_blob_paths(safe, lap)
        mp4 = f"{folder}/{safe}_lap{lap:03d}.mp4"
        try:
            fs.invalidate_cache(folder)
            if fs.exists(mp4):
                return mp4, sprite_path, sidecar_path, safe
        except Exception:
            continue
    return None


def _probe_dimensions(mp4_path: str) -> tuple[int, int] | None:
    """ffprobe a local MP4 for (width, height). Synchronous — call inside
    asyncio.to_thread."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x",
                mp4_path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        if proc.returncode != 0:
            return None
        out = proc.stdout.decode(errors="replace").strip()
        if "x" not in out:
            return None
        w_s, h_s = out.split("x", 1)
        return int(w_s), int(h_s)
    except Exception:
        return None


def _run_sprite_ffmpeg(
    mp4_path: str, sprite_path: str, duration_ms: int, tile_w: int, tile_h: int
) -> bool:
    """Run the single ffmpeg pass that produces the sprite. Returns True on
    success. Synchronous — call inside asyncio.to_thread."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("ffmpeg binary not found on PATH; cannot lazy-generate sprite")
        return False
    duration_s = max(duration_ms, 1) / 1000.0
    vf = (
        f"fps={_SPRITE_TILES}/{duration_s:.6f},"
        f"scale={tile_w}:{tile_h}:force_original_aspect_ratio=decrease,"
        f"pad={tile_w}:{tile_h}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"tile={_SPRITE_COLS}x{_SPRITE_ROWS}"
    )
    try:
        proc = subprocess.run(
            [
                ffmpeg, "-y",
                "-i", mp4_path,
                "-vf", vf,
                "-frames:v", "1",
                "-q:v", "5",
                sprite_path,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=180,
        )
        if proc.returncode != 0:
            logger.warning(
                "Lazy sprite ffmpeg failed (%d): %s",
                proc.returncode,
                proc.stderr.decode(errors="replace")[:300],
            )
            return False
        return True
    except Exception:
        logger.exception("Lazy sprite ffmpeg crashed: %s", mp4_path)
        return False


def _read_sidecar(sidecar_blob: str) -> dict | None:
    """Read + parse the sidecar JSON from blob storage. Returns None if the
    blob is missing or the body isn't valid JSON."""
    fs = get_blob_fs()
    if not fs:
        return None
    try:
        body = fs.cat(sidecar_blob)
    except FileNotFoundError:
        return None
    except Exception:
        logger.exception("Failed to read sidecar for sprite merge: %s", sidecar_blob)
        return None
    try:
        return json.loads(body)
    except Exception:
        logger.exception("Sidecar JSON parse failed: %s", sidecar_blob)
        return None


# ---------------------------------------------------------------------------
# Speculative `thumbs` metadata for old laps whose sidecar predates the
# recorder change (no `thumbs` block on disk). We synthesise a block from a
# quick ffprobe of the MP4 head — moov atom is at the front thanks to
# `+faststart` (ac_video_streaming/video_recorder.py), so a 256 KB head-read
# is normally enough. The block is injected into the metadata response so the
# frontend's existing logic kicks in: it requests /thumbs.jpg, the lazy
# generation path runs, the real sidecar gets updated. Without this hydration,
# the frontend bails before ever asking for the sprite and old laps stay
# preview-less forever.
#
# Result is cached in a small LRU keyed on (session_id, lap) so reloading the
# same lap doesn't re-ffprobe. Cleared on process restart; no persistence.
# ---------------------------------------------------------------------------

_SPECULATIVE_HEAD_BYTES = 256 * 1024


def _ffprobe_local_dims_and_duration(mp4_local: str) -> tuple[int, int, int] | None:
    """ffprobe a local MP4 for (width, height, duration_ms). Returns None on
    any failure. Synchronous."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                mp4_local,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        if proc.returncode != 0:
            return None
        lines = [ln.strip() for ln in proc.stdout.decode(errors="replace").splitlines() if ln.strip()]
        # Output order: width, height, duration (one per line).
        if len(lines) < 3:
            return None
        w = int(lines[0])
        h = int(lines[1])
        dur_ms = int(round(float(lines[2]) * 1000))
        if w <= 0 or h <= 0 or dur_ms <= 0:
            return None
        return w, h, dur_ms
    except Exception:
        return None


@functools.lru_cache(maxsize=100)
def _speculative_thumbs_cached(safe_session: str, lap: int, mp4_blob_path: str) -> dict | None:
    """Cached synthesis of a `thumbs` block for an old lap missing one.

    Strategy:
      1. Open the MP4 in the blob filesystem and read just the first ~256 KB.
         Faststart puts the moov atom at the front, so ffprobe extracts
         width/height/duration without us ever downloading the full file.
      2. If head-only ffprobe fails (pre-faststart lap, or unusual mux),
         fall back to a full download. Slower but correct.
      3. Compute tile_h=90, tile_w preserves aspect (rounded to even px to
         match the recorder/proxy ffmpeg conventions). ms_per_tile is
         duration_ms/100 rounded.

    Cache key includes `mp4_blob_path` so a rare path-variant change for the
    same logical lap won't reuse a stale dim. lru_cache is process-local;
    eviction is automatic at maxsize=100.
    """
    fs = get_blob_fs()
    if not fs:
        return None

    tmp_dir = tempfile.mkdtemp(prefix="thumbs_meta_")
    head_local = os.path.join(tmp_dir, "head.mp4")
    full_local = os.path.join(tmp_dir, "full.mp4")
    try:
        # 1. Try head-read first — cheap and usually sufficient with faststart.
        dims = None
        try:
            with fs.open(mp4_blob_path, "rb") as fh:
                head_bytes = fh.read(_SPECULATIVE_HEAD_BYTES)
            if head_bytes:
                with open(head_local, "wb") as out:
                    out.write(head_bytes)
                dims = _ffprobe_local_dims_and_duration(head_local)
        except Exception:
            logger.exception("Speculative thumbs head-read failed: %s", mp4_blob_path)

        # 2. Fallback: full download. Only triggered for pre-faststart laps or
        #    edge muxes where the moov atom isn't in the first 256 KB.
        if dims is None:
            logger.info(
                "Speculative thumbs head-read insufficient; falling back to full download: %s",
                mp4_blob_path,
            )
            try:
                full_bytes = fs.cat(mp4_blob_path)
                with open(full_local, "wb") as out:
                    out.write(full_bytes)
                del full_bytes
                dims = _ffprobe_local_dims_and_duration(full_local)
            except Exception:
                logger.exception("Speculative thumbs full-download failed: %s", mp4_blob_path)
                return None

        if dims is None:
            return None
        src_w, src_h, duration_ms = dims
        tile_h = _SPRITE_TILE_H
        tile_w = max(2, int(round(tile_h * src_w / src_h)))
        if tile_w % 2:
            tile_w += 1

        # `url` is just the basename — same shape the eager-recorder writes
        # and the lazy-generation path uses. Frontend builds the full
        # /api/video/{sid}/{lap}/thumbs.jpg path itself; this string is a
        # no-op there but kept for symmetry with the on-disk schema.
        base = f"{safe_session}_lap{lap:03d}.thumbs.jpg"
        return {
            "url": base,
            "tiles": _SPRITE_TILES,
            "cols": _SPRITE_COLS,
            "rows": _SPRITE_ROWS,
            "tile_w": tile_w,
            "tile_h": tile_h,
            "ms_per_tile": int(round(duration_ms / _SPRITE_TILES)),
            "duration_ms": duration_ms,
            "_speculative": True,
        }
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


def _generate_sprite_sync(mp4_blob_path: str, sprite_blob_path: str, sidecar_blob_path: str) -> bytes | None:
    """Synchronous slow path: download MP4, ffprobe, ffmpeg, upload sprite,
    merge thumbs block into sidecar JSON. Returns the sprite bytes on success.
    Run inside asyncio.to_thread so the FastAPI event loop stays responsive."""
    fs = get_blob_fs()
    if not fs:
        return None
    tmp_dir = tempfile.mkdtemp(prefix="thumbs_")
    mp4_local = os.path.join(tmp_dir, "in.mp4")
    sprite_local = os.path.join(tmp_dir, "out.thumbs.jpg")
    try:
        # 1. Download MP4 to a temp file. fs.cat returns bytes; for a
        #    multi-hundred-MB lap that's a transient memory hit but avoids
        #    needing a streaming download API on the blob filesystem.
        try:
            mp4_bytes = fs.cat(mp4_blob_path)
        except Exception:
            logger.exception("Failed to download MP4 for sprite gen: %s", mp4_blob_path)
            return None
        with open(mp4_local, "wb") as fh:
            fh.write(mp4_bytes)
        del mp4_bytes  # let GC reclaim

        # 2. Probe + decide tile dims (height=90, width preserves aspect).
        dims = _probe_dimensions(mp4_local)
        if dims is None or dims[0] <= 0 or dims[1] <= 0:
            logger.warning("ffprobe failed for %s, skipping sprite gen", mp4_blob_path)
            return None
        src_w, src_h = dims
        tile_h = _SPRITE_TILE_H
        tile_w = max(2, int(round(tile_h * src_w / src_h)))
        if tile_w % 2:
            tile_w += 1

        # 3. Determine duration from the existing sidecar if available so the
        #    sample times align with what the frontend reads. Fall back to an
        #    ffprobe-of-MP4 duration if no sidecar yet.
        existing_sidecar = _read_sidecar(sidecar_blob_path)
        duration_ms = 0
        if existing_sidecar:
            duration_ms = int(existing_sidecar.get("duration_ms") or 0)
        if duration_ms <= 0:
            ffprobe = shutil.which("ffprobe")
            if ffprobe:
                try:
                    proc = subprocess.run(
                        [
                            ffprobe, "-v", "error",
                            "-show_entries", "format=duration",
                            "-of", "csv=p=0",
                            mp4_local,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        timeout=15,
                    )
                    if proc.returncode == 0:
                        duration_ms = int(round(float(proc.stdout.decode().strip()) * 1000))
                except Exception:
                    pass
        if duration_ms <= 0:
            logger.warning("Could not determine duration for %s; skipping sprite", mp4_blob_path)
            return None

        # 4. Run ffmpeg.
        if not _run_sprite_ffmpeg(mp4_local, sprite_local, duration_ms, tile_w, tile_h):
            return None
        with open(sprite_local, "rb") as fh:
            sprite_bytes = fh.read()
        if not sprite_bytes:
            return None

        # 5. Upload sprite.
        try:
            fs.pipe(sprite_blob_path, sprite_bytes)
        except Exception:
            logger.exception("Failed to upload generated sprite: %s", sprite_blob_path)
            # Still return bytes to the caller — at least the current request
            # gets a response; future requests will retry generation.
            return sprite_bytes

        # 6. Merge `thumbs` block into the existing sidecar (or write a stub).
        #    Read-modify-write: blob writes are atomic at the object level,
        #    so partial reads are not a concern. If the sidecar is missing
        #    we skip — the Telemetry Explorer's main /api/video/{sid}/{lap}
        #    handler degrades gracefully on missing sidecars already.
        thumbs_block = {
            "url": os.path.basename(sprite_blob_path),
            "tiles": _SPRITE_TILES,
            "cols": _SPRITE_COLS,
            "rows": _SPRITE_ROWS,
            "tile_w": tile_w,
            "tile_h": tile_h,
            "ms_per_tile": int(round(duration_ms / _SPRITE_TILES)),
            "duration_ms": duration_ms,
        }
        if existing_sidecar is not None:
            existing_sidecar["thumbs"] = thumbs_block
            try:
                fs.pipe(sidecar_blob_path, json.dumps(existing_sidecar).encode())
            except Exception:
                logger.exception("Failed to update sidecar with thumbs block: %s", sidecar_blob_path)
        return sprite_bytes
    finally:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception:
            pass


async def _ensure_sprite(
    mp4_blob_path: str, sprite_blob_path: str, sidecar_blob_path: str,
    session_id: str, lap: int,
) -> bytes | None:
    """Lazy-generate the sprite under a per-lap asyncio.Lock. Re-checks the
    blob inside the lock to absorb concurrent first-request races."""
    fs = get_blob_fs()
    if not fs:
        return None
    key = (session_id, lap)
    lock = _sprite_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _sprite_locks[key] = lock
    try:
        async with lock:
            # Re-check — another waiter may have just finished generating.
            try:
                if fs.exists(sprite_blob_path):
                    try:
                        return fs.cat(sprite_blob_path)
                    except Exception:
                        logger.exception("Failed to read just-generated sprite: %s", sprite_blob_path)
                        return None
            except Exception:
                # exists() failure shouldn't be fatal; fall through to generation.
                pass
            return await asyncio.to_thread(
                _generate_sprite_sync, mp4_blob_path, sprite_blob_path, sidecar_blob_path
            )
    finally:
        # Clean up the lock entry so the registry doesn't leak. Safe even
        # under contention — a follow-up request just re-creates the lock.
        _sprite_locks.pop(key, None)


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
    fs = get_blob_fs()
    if not fs:
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
        sidecar_bytes = fs.cat(sidecar_path)
        sync = json.loads(sidecar_bytes)
    except FileNotFoundError:
        pass
    except Exception:
        logger.exception("Failed to read sidecar JSON: %s", sidecar_path)

    # Hydrate speculative `thumbs` for old laps so the frontend's drag-preview
    # path engages and triggers lazy /thumbs.jpg generation. Without this the
    # check `if (!syncMeta.thumbs)` in thumb-preview.js short-circuits and the
    # lazy fallback never fires for back-catalogue laps. We only hydrate when
    # the sidecar parsed cleanly AND lacks a real `thumbs` block — never
    # overwrite a recorder- or lazy-generation-written block.
    if sync is not None and not sync.get("thumbs"):
        # session_id format on disk = parent folder name after `session_id=`.
        # Path shape: {prefix}/session_id={safe}/{safe}_lap{nnn}.mp4
        safe_session = os.path.basename(os.path.dirname(mp4_path)).split("session_id=", 1)[-1]
        try:
            speculative = _speculative_thumbs_cached(safe_session, lap, mp4_path)
        except Exception:
            logger.exception("Speculative thumbs synthesis crashed: %s lap %d", session_id, lap)
            speculative = None
        if speculative is not None:
            sync["thumbs"] = speculative

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
    fs = get_blob_fs()
    if not fs:
        raise HTTPException(503, "Blob storage not connected")

    # Try session_id format variants to find the actual blob path
    mp4_path = None
    total = 0
    for safe in _session_blob_variants(session_id):
        folder = f"{config.BLOB_VIDEO_PREFIX}/session_id={safe}"
        base = f"{safe}_lap{lap:03d}"
        candidate = f"{folder}/{base}.mp4"
        try:
            info = fs.info(candidate)
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

    # Cache-Control deliberately differs by response type:
    #   - 200 (full file, no Range): max-age=300 is safe — same URL, same body.
    #   - 206 (partial content): no-store. Range-aware caching is fragile —
    #     a stale 206 cached response served against a different Range gives
    #     the browser bytes that don't match its request, the decoder gets
    #     incomplete data, and playback hangs on backward seeks. `Vary: Range`
    #     would be the correct standards way, but not all middleboxes honour
    #     it; no-store is the bulletproof option for the partial-content path.
    common_full = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=300",
    }
    common_range = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Vary": "Range",
    }

    if not range:
        try:
            data = fs.cat(mp4_path)
        except FileNotFoundError as e:
            raise HTTPException(404, f"Video not found: session={session_id} lap={lap}") from e
        except Exception as e:
            logger.exception("Failed to fetch MP4 from blob: %s", mp4_path)
            raise HTTPException(500, "Failed to fetch video") from e
        return Response(
            content=data,
            media_type="video/mp4",
            headers={**common_full, "Content-Length": str(len(data))},
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
            headers={**common_range, "Content-Range": f"bytes */{total}"},
        )
    length = end - start + 1

    try:
        with fs.open(mp4_path, "rb") as fh:
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
            **common_range,
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{total}",
        },
    )


@router.get("/api/video/{session_id}/{lap}/thumbs.jpg")
async def get_thumbs(session_id: str, lap: int):
    """Serve the marker-drag preview sprite. Fast path returns the cached
    blob; slow path lazy-generates it via ffmpeg, uploads back to blob, and
    folds the `thumbs` block into the sidecar JSON. See spec
    dev-planning/marker-drag-frame-preview/spec.md §5.2.
    """
    fs = get_blob_fs()
    if not fs:
        raise HTTPException(503, "Blob storage not connected")

    paths = _find_sprite_paths(session_id, lap)
    if not paths:
        raise HTTPException(404, f"Video not found: session={session_id} lap={lap}")
    mp4_path, sprite_path, sidecar_path, _safe_session = paths

    # Fast path — sprite already exists in blob.
    try:
        if fs.exists(sprite_path):
            try:
                data = fs.cat(sprite_path)
                return Response(
                    content=data,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"},
                )
            except Exception as e:
                logger.exception("Failed to read existing sprite: %s", sprite_path)
                raise HTTPException(500, "Failed to read sprite") from e
    except HTTPException:
        raise
    except Exception:
        # exists() may raise on transient blob errors; fall through to lazy
        # generation rather than 500-ing on a recoverable hiccup.
        pass

    # Slow path — generate, upload, return bytes.
    try:
        sprite_bytes = await _ensure_sprite(
            mp4_path, sprite_path, sidecar_path, session_id, lap
        )
    except Exception as e:
        logger.exception("Lazy sprite generation failed for %s lap %d", session_id, lap)
        raise HTTPException(500, "Failed to generate sprite") from e

    if not sprite_bytes:
        raise HTTPException(500, "Sprite generation produced no output")

    return Response(
        content=sprite_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )
