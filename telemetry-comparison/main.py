"""
Telemetry Comparison — FastAPI service for cross-run/lap telemetry visualization.

Queries Hive-partitioned Parquet data in QuixLake via SQL (DuckDB) and serves
an interactive Plotly.js UI for overlaying telemetry from different sessions/laps.

Module layout:
  - config.py           — env vars, paths, rendering constants
  - partition_filter.py — SQL-safe WHERE builder for partition cols
  - partition_walker.py — QuixLake /partitions tree walker (used by /api/sessions)
  - track_loader.py     — /api/track + /api/track/config (APIRouter)
  - video_proxy.py      — /api/video/... MP4 + sidecar proxy (APIRouter)
  - main.py (this file) — FastAPI app + plotting routes (/api/sessions,
                           /telemetry, /channels, /health) + static SPA mount
"""

from __future__ import annotations

import io
import json
import logging
import os

import httpx
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import chat
import config
import track_loader
import video_proxy
from partition_filter import _build_partition_filter
from partition_walker import _walk_partition_tree

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
# Uvicorn's own handler already formats its lifecycle/error/access logs; stop
# them from bubbling up to the root handler installed above or we double-log.
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    logging.getLogger(_name).propagate = False
# httpx logs every outbound request at INFO — one /api/sessions call emits
# ~30 lines. We don't need that in either dev or prod output. httpcore (the
# transport layer) is muted too because at DEBUG it dumps full request
# headers including the bearer Authorization token.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

app = FastAPI(title="Telemetry Comparison")
app.include_router(chat.router)
app.include_router(track_loader.router)
app.include_router(video_proxy.router)

# Load channel metadata at startup.
with open(config.CHANNELS_FILE) as f:
    _raw = json.load(f)
CHANNELS = {k: v for k, v in _raw.items() if not k.startswith("_")}


# Shared async client for QuixLake /query calls. Same rationale as
# partition_walker._http_client: amortise TLS + connection pool across all
# requests, and use an async transport so concurrent /api/telemetry calls
# (the Plot-button fan-out) actually overlap on the lake instead of
# serialising on a sync client per request.
_lake_http = httpx.AsyncClient(
    timeout=60.0,
    limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
)


def sanitize_df(df):
    """Replace NaN/Inf with None for JSON serialization."""
    return df.where(df.notna(), None)


async def _lake_query(sql: str) -> pd.DataFrame:
    """POST a SQL string to QuixLake's /query endpoint, parse the CSV reply.

    Raises HTTPException(502) on non-200 lake responses. The caller MUST
    catch HTTPException explicitly and re-raise (see /api/telemetry) — the
    generic `except Exception` would otherwise reframe it as a 500.
    """
    if not config.QUIXLAKE_URL or not config.QUIX_LAKE_TOKEN:
        missing = [
            name
            for name, val in (
                ("QUIXLAKE_URL", config.QUIXLAKE_URL),
                ("QUIX_LAKE_TOKEN", config.QUIX_LAKE_TOKEN),
            )
            if not val
        ]
        raise RuntimeError(
            f"Missing required env var(s): {', '.join(missing)}. "
            "Set them in .env or the environment before starting the service."
        )
    r = await _lake_http.post(
        f"{config.QUIXLAKE_URL}/query",
        content=sql,
        headers={
            "Authorization": f"Bearer {config.QUIX_LAKE_TOKEN}",
            "Content-Type": "text/plain",
        },
    )
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Data lake returned {r.status_code} {r.reason_phrase}",
        )
    return pd.read_csv(io.StringIO(r.text))


@app.get("/api/sessions")
async def list_sessions(
    environment: str = "",
    test_rig: str = "",
    experiment: str = "",
    driver: str = "",
    track: str = "",
    carModel: str = "",
    session_id: str = "",
):
    """Return partition-column combinations in the lake.

    With no query params → full tree walk, every session (direct-access UX).
    With partition column query params → walk narrowed to matching branch
    (deep-link fast path, typically one session, ~300-400 ms).
    """
    filters = {
        c: v
        for c, v in {
            "environment": environment,
            "test_rig": test_rig,
            "experiment": experiment,
            "driver": driver,
            "track": track,
            "carModel": carModel,
            "session_id": session_id,
        }.items()
        if v
    }
    try:
        sessions = await _walk_partition_tree("", 0, filters or None)
        return JSONResponse(content={"sessions": sessions})
    except httpx.HTTPStatusError as e:
        # QuixLake returned an error. Surface the real upstream status so
        # the frontend toast can say "403 Forbidden" instead of a generic 500.
        logger.warning("QuixLake returned %s for %s", e.response.status_code, e.request.url)
        raise HTTPException(
            status_code=502,
            detail=f"Data lake returned {e.response.status_code} {e.response.reason_phrase}",
        ) from e
    except httpx.TimeoutException as e:
        logger.warning("QuixLake timed out: %s", e)
        raise HTTPException(status_code=504, detail=f"Data lake timed out: {e}") from e
    except Exception as e:
        logger.exception("Failed to list sessions")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/telemetry")
async def get_telemetry(
    lap: int,
    signals: str = "speedKmh,gas,brake,steerAngle",
    environment: str = "",
    test_rig: str = "",
    experiment: str = "",
    driver: str = "",
    track: str = "",
    carModel: str = "",
    session_id: str = "",
):
    """Get telemetry data for a specific run/lap, ordered by track position."""
    signal_list = [s.strip() for s in signals.split(",") if s.strip()]
    for s in signal_list:
        if not s.isidentifier():
            raise HTTPException(status_code=400, detail=f"Invalid signal name: {s}")

    columns = ", ".join(signal_list)
    try:
        where = _build_partition_filter(
            environment=environment,
            test_rig=test_rig,
            experiment=experiment,
            driver=driver,
            track=track,
            carModel=carModel,
            session_id=session_id,
            lap=lap,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        sql = f"""
            SELECT
                normalizedCarPosition,
                timestamp_ms,
                {columns}
            FROM {config.TABLE_NAME}
            {where}
        """
        df = await _lake_query(sql)
        # Lake response isn't ordered (we dropped SQL ORDER BY — sorting in
        # pandas is ~5 ms vs ~60 ms warm / multi-second cold for DuckDB to
        # sort). Frontend `downsample()` walks x by index so it must arrive
        # sorted on normalizedCarPosition.
        df = df.sort_values("normalizedCarPosition").reset_index(drop=True)
        df = sanitize_df(df)

        # First lap: trim the approach to the start line (pit exit / grid).
        if lap == 1 and not df.empty:
            by_time = df.sort_values("timestamp_ms")
            ncp = by_time["normalizedCarPosition"].values
            trimmed = False
            # Case 1: race start — data wraps from near 1 to near 0.
            for i in range(1, len(ncp)):
                if ncp[i - 1] > 0.9 and ncp[i] < 0.1:
                    df = by_time.iloc[i:].sort_values("normalizedCarPosition")
                    trimmed = True
                    break
            # Case 2: pit start — normPos only goes from ~0.7 to ~1.0,
            # no wrap. This is a pure out-lap with no full-circuit data.
            if not trimmed:
                min_ncp = df["normalizedCarPosition"].min()
                if min_ncp is not None and min_ncp > 0.1:
                    df = df.iloc[0:0]

        return JSONResponse(
            content={
                "session_id": session_id,
                "lap": lap,
                "signals": signal_list,
                "count": len(df),
                "data": df.to_dict(orient="list"),
            }
        )
    except HTTPException:
        # _lake_query already mapped the upstream status (e.g. 502); preserve it.
        raise
    except httpx.TimeoutException as e:
        logger.warning("QuixLake timed out: %s", e)
        raise HTTPException(status_code=504, detail=f"Data lake timed out: {e}") from e
    except Exception as e:
        logger.exception("Failed to get telemetry")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/api/channels")
async def list_channels():
    """Return channel metadata grouped by category."""
    return JSONResponse(content=CHANNELS)


@app.get("/health")
async def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (config.STATIC_DIR / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
