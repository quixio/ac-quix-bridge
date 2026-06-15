"""FastAPI app factory and read-only routes for the track viewer.

Data contract (verified against track-importer/importer.py):
  - `_id` is the literal string `"<track>/<layout>"` (importer.py:130).
  - `points[]` elements are dicts with 14 named numeric fields; this viewer
    reads only `x`, `z`, `radius_m` (importer.py:23-35, 53-60).
  - `corners[]` is passed through as stored from the source corners JSON
    (importer.py:122) — records with id/type/direction/distance_*_m/
    *_radius_m fields. Returned but not required to render in Phase 1.

Orientation note: AC tracks have NO universal north. The renderer plots raw
`x` (horizontal) vs `z` (vertical) at equal aspect ratio and does NOT
auto-orient. A layout may appear rotated relative to a real-world map — that
is expected, not a bug.
"""

import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pymongo.errors import PyMongoError

from . import mongo
from .settings import MongoSettings, ViewerSettings

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# Summary fields surfaced by GET /api/tracks. `points` and `corners` are large
# and explicitly excluded from the projection.
_SUMMARY_FIELDS = (
    "track",
    "trackConfiguration",
    "layout",
    "length_m",
    "n_points",
    "n_corners",
)


def create_app() -> FastAPI:
    mongo_settings = MongoSettings()  # type: ignore[call-arg]
    viewer_settings = ViewerSettings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        # connect() is lazy and cannot crash on an unreachable host, so a
        # bad MONGO_HOST does not take the process down — /healthz and the UI
        # report it instead.
        try:
            mongo.connect(mongo_settings)
            logger.info(
                "Mongo client configured for db=%s host=%s (lazy; not yet pinged)",
                mongo_settings.database,
                mongo_settings.host,
            )
        except Exception:  # pragma: no cover - construction is lazy
            logger.exception("Failed to configure Mongo client")
        yield
        mongo.disconnect()

    application = FastAPI(
        title="Track Viewer",
        description="Read-only viewer for imported track_layouts.",
        lifespan=lifespan,
    )

    @application.get("/healthz")
    def healthz() -> JSONResponse:
        body: dict[str, Any] = {
            "mongo_ok": False,
            "db": mongo_settings.database,
            "collection": viewer_settings.collection,
            "doc_count": None,
            "error": None,
        }
        try:
            db = mongo.get_mongo()
            db.command("ping")
            body["doc_count"] = db[viewer_settings.collection].count_documents({})
            body["mongo_ok"] = True
            return JSONResponse(body, status_code=200)
        except (PyMongoError, RuntimeError) as exc:
            body["error"] = str(exc)
            return JSONResponse(body, status_code=503)

    @application.get("/api/tracks")
    def list_tracks() -> JSONResponse:
        projection = {field: 1 for field in _SUMMARY_FIELDS}
        # _id is the "<track>/<layout>" string; keep it (default included).
        try:
            db = mongo.get_mongo()
            cursor = (
                db[viewer_settings.collection]
                .find({}, projection)
                .sort([("track", 1), ("layout", 1)])
            )
            summaries = [
                {
                    "_id": doc.get("_id"),
                    "track": doc.get("track"),
                    "trackConfiguration": doc.get("trackConfiguration"),
                    "layout": doc.get("layout"),
                    "length_m": doc.get("length_m"),
                    "n_points": doc.get("n_points"),
                    "n_corners": doc.get("n_corners"),
                }
                for doc in cursor
            ]
            return JSONResponse(summaries, status_code=200)
        except (PyMongoError, RuntimeError) as exc:
            return JSONResponse(
                {"error": f"Cannot reach MongoDB: {exc}"}, status_code=503
            )

    @application.get("/api/tracks/{layout_id:path}/geometry")
    def geometry(layout_id: str) -> JSONResponse:
        try:
            db = mongo.get_mongo()
            # _id is stored as the literal "<track>/<layout>" string
            # (importer.py:130), so match it directly.
            doc = db[viewer_settings.collection].find_one({"_id": layout_id})
        except (PyMongoError, RuntimeError) as exc:
            return JSONResponse(
                {"error": f"Cannot reach MongoDB: {exc}"}, status_code=503
            )

        if doc is None:
            return JSONResponse(
                {"error": f"No layout with id {layout_id!r}"}, status_code=404
            )

        raw_points: list[dict[str, Any]] = doc.get("points", []) or []
        n_points = len(raw_points)
        kept = _downsample(raw_points, viewer_settings.max_points)

        # Emit only the 3 fields the renderer needs: [x, z, radius_m].
        points = [
            [p.get("x"), p.get("z"), p.get("radius_m")] for p in kept
        ]

        return JSONResponse(
            {
                "id": doc.get("_id"),
                "track": doc.get("track"),
                "trackConfiguration": doc.get("trackConfiguration"),
                "layout": doc.get("layout"),
                "length_m": doc.get("length_m"),
                "n_points": n_points,
                "n_points_returned": len(points),
                "n_corners": doc.get("n_corners"),
                "downsampled": len(points) < n_points,
                "points": points,
                "corners": doc.get("corners", []) or [],
            },
            status_code=200,
        )

    @application.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    # Static assets (app.js etc.) served under /static. index.html references
    # them with RELATIVE URLs (./static/app.js) so a path-rewriting ingress
    # prefix does not break asset loading.
    application.mount(
        "/static", StaticFiles(directory=_STATIC_DIR), name="static"
    )

    return application


def _downsample(points: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Uniformly stride-sample `points` down to at most `cap`, always keeping
    the first and last point so the polyline still closes the loop."""
    n = len(points)
    if n <= cap or cap <= 0:
        return points
    step = math.ceil(n / cap)
    sampled = points[::step]
    if sampled[-1] is not points[-1]:
        sampled.append(points[-1])
    return sampled
