"""
Telemetry Comparison — FastAPI service for cross-run/lap telemetry visualization.

Queries Hive-partitioned Parquet data in QuixLake via SQL (DuckDB) and serves
an interactive Plotly.js UI for overlaying telemetry from different sessions/laps.
"""

import json
import os
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from quixlake import QuixLakeClient

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="Telemetry Comparison")

TABLE_NAME = os.getenv("TABLE_NAME", "ac_telemetry")
QUIXLAKE_URL = os.getenv("QUIXLAKE_URL")
QUIX_LAKE_TOKEN = os.getenv("QUIX_LAKE_TOKEN")

STATIC_DIR = Path(__file__).parent / "static"
CHANNELS_FILE = Path(__file__).parent / "channels.json"

# Load channel metadata at startup
with open(CHANNELS_FILE) as f:
    _raw = json.load(f)
CHANNELS = {k: v for k, v in _raw.items() if not k.startswith("_")}


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
            # Try exact match in both formats (raw ISO and normalized)
            normalized = val.replace('T', ' ').rstrip('Z')
            with_t = val if 'T' in val else val.replace(' ', 'T') + 'Z'
            clauses.append(
                f"(session_id = '{val}'"
                f" OR session_id = '{normalized}'"
                f" OR session_id = '{with_t}')"
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
# Static files & SPA
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
