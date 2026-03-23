"""
Config Form — A simple UI for creating experiment configs.

Submits configs to the Dynamic Configuration Manager REST API,
which handles versioning and Kafka event publishing.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
CONFIG_MANAGER_URL = os.environ.get("CONFIG_MANAGER_URL", "https://config-api-svc-quixers-acquixbridge-dev.az-france-0.app.quix.io")
CONFIG_TYPE = os.environ.get("CONFIG_TYPE", "experiment")
TARGET_KEY = os.environ.get("TARGET_KEY", "*")
API_BASE = f"{CONFIG_MANAGER_URL}/api/v1"

api = FastAPI()


async def _find_config_id() -> str | None:
    """Search for the existing config by type and target key, return its ID or None."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_BASE}/configurations",
            params={"configType": CONFIG_TYPE, "targetKey": TARGET_KEY},
            timeout=5.0,
        )
        if resp.status_code == 200:
            configs = resp.json()
            if isinstance(configs, list) and len(configs) > 0:
                return configs[0].get("id") or configs[0].get("_id")
            if isinstance(configs, dict) and configs.get("items"):
                return configs["items"][0].get("id") or configs["items"][0].get("_id")
    return None


@api.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@api.get("/api/current")
async def get_current_config():
    """Fetch the current active config from the Dynamic Configuration Manager."""
    try:
        config_id = await _find_config_id()
        if not config_id:
            return {"error": "No config found"}

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_BASE}/configurations/{config_id}/content",
                timeout=5.0,
            )
            if resp.status_code == 200:
                return {"content": resp.json(), "config_id": config_id}
            return {"error": "Could not fetch content", "status": resp.status_code}
    except Exception as e:
        logger.exception("Failed to fetch current config")
        return {"error": str(e)}


@api.post("/api/submit")
async def submit_config(request: Request):
    """Create or update the experiment config in the Dynamic Configuration Manager."""
    form_data = await request.json()

    # Auto-generate test_id
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    driver = form_data.get("driver", "unknown")
    car = form_data.get("car", "unknown")
    track = form_data.get("track", "unknown")
    beers = form_data.get("beers", 0)
    test_id = f"run_{ts}_{driver}_{car}_{track}_{beers}beers"

    config_content = {
        "test_id": test_id,
        "timestamp": ts,
        "environment": form_data.get("environment", ""),
        "test_rig": form_data.get("test_rig", ""),
        "experiment_id": form_data.get("experiment_id", ""),
        "driver": driver,
        "car": car,
        "track": track,
        "beers": int(beers),
    }

    try:
        config_id = await _find_config_id()

        async with httpx.AsyncClient() as client:
            if config_id:
                # Update existing config — creates a new version
                resp = await client.put(
                    f"{API_BASE}/configurations/{config_id}",
                    json={
                        "configType": CONFIG_TYPE,
                        "targetKey": TARGET_KEY,
                        "content": config_content,
                    },
                    timeout=10.0,
                )
            else:
                # Create new config
                resp = await client.post(
                    f"{API_BASE}/configurations",
                    json={
                        "configType": CONFIG_TYPE,
                        "targetKey": TARGET_KEY,
                        "content": config_content,
                    },
                    timeout=10.0,
                )

            if resp.status_code in (200, 201):
                logger.info("Config submitted: %s (id=%s)", test_id, config_id or "new")
                return {"ok": True, "test_id": test_id, "config": config_content}
            else:
                logger.error("Config Manager returned %d: %s", resp.status_code, resp.text)
                return JSONResponse(
                    status_code=resp.status_code,
                    content={"error": resp.text},
                )
    except Exception as e:
        logger.exception("Failed to submit config")
        return JSONResponse(status_code=500, content={"error": str(e)})


@api.get("/{full_path:path}")
async def fallback(full_path: str = ""):
    """Catch-all for Quix proxy paths — must be last."""
    return FileResponse(str(STATIC_DIR / "index.html"))
