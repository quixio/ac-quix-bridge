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
API_BASE = f"{CONFIG_MANAGER_URL}/api/v1"
AUTH_TOKEN = os.environ.get("Quix__Sdk__Token", "")

api = FastAPI()


def _auth_headers() -> dict:
    if AUTH_TOKEN:
        return {"authorization": AUTH_TOKEN}
    return {}


async def _find_config_id(target_key: str) -> str | None:
    """Search for the existing config by type and target key, return its ID or None."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{API_BASE}/configurations",
            headers=_auth_headers(),
            timeout=5.0,
        )
        logger.info("Search configs: %d %s", resp.status_code, resp.text[:500])
        if resp.status_code == 200:
            data = resp.json()
            configs = data if isinstance(data, list) else data.get("data", data.get("items", []))
            for cfg in configs:
                meta = cfg.get("metadata", {})
                cfg_type = meta.get("type", "")
                cfg_key = meta.get("target_key", "")
                if cfg_type == CONFIG_TYPE and cfg_key == target_key:
                    config_id = cfg.get("id") or cfg.get("_id")
                    logger.info("Found existing config: %s (target_key=%s)", config_id, target_key)
                    return config_id
    return None


@api.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@api.get("/api/current")
async def get_current_config():
    """Fetch current configs for all rigs."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_BASE}/configurations",
                headers=_auth_headers(),
                timeout=5.0,
            )
            if resp.status_code != 200:
                return {"error": "Could not fetch configs", "status": resp.status_code}

            data = resp.json()
            configs = data if isinstance(data, list) else data.get("data", data.get("items", []))

            results = {}
            for cfg in configs:
                meta = cfg.get("metadata", {})
                if meta.get("type") == CONFIG_TYPE:
                    config_id = cfg.get("id") or cfg.get("_id")
                    target_key = meta.get("target_key", "")
                    # Fetch content
                    content_resp = await client.get(
                        f"{API_BASE}/configurations/{config_id}/content",
                        headers=_auth_headers(),
                        timeout=5.0,
                    )
                    if content_resp.status_code == 200:
                        results[target_key] = content_resp.json()

            return {"configs": results}
    except Exception as e:
        logger.exception("Failed to fetch current config")
        return {"error": str(e)}


@api.post("/api/submit")
async def submit_config(request: Request):
    """Create or update the experiment config in the Dynamic Configuration Manager."""
    form_data = await request.json()

    target_key = form_data.get("rig_hostname", "*")

    # Auto-generate test_id
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    driver = form_data.get("driver", "unknown")
    beers = form_data.get("beers", 0)
    test_id = f"run_{ts}_{driver}_{beers}beers"

    config_content = {
        "test_id": test_id,
        "timestamp": ts,
        "environment": form_data.get("environment", ""),
        "test_rig": form_data.get("test_rig", ""),
        "experiment_id": form_data.get("experiment_id", ""),
        "driver": driver,
        "beers": int(beers),
    }

    try:
        config_id = await _find_config_id(target_key)

        async with httpx.AsyncClient() as client:
            if config_id:
                # Update existing config — creates a new version
                resp = await client.put(
                    f"{API_BASE}/configurations/{config_id}",
                    json={
                        "metadata": {
                            "category": "ac-telemetry",
                            "valid_from": ts,
                        },
                        "content": config_content,
                    },
                    headers=_auth_headers(),
                    timeout=10.0,
                )
            else:
                # Create new config for this rig
                resp = await client.post(
                    f"{API_BASE}/configurations",
                    json={
                        "metadata": {
                            "type": CONFIG_TYPE,
                            "target_key": target_key,
                            "category": "ac-telemetry",
                            "valid_from": ts,
                        },
                        "content": config_content,
                    },
                    headers=_auth_headers(),
                    timeout=10.0,
                )

            logger.info("Config API response: %d %s", resp.status_code, resp.text[:300])

            if resp.status_code in (200, 201):
                logger.info("Config submitted: %s (target_key=%s, id=%s)", test_id, target_key, config_id or "new")
                return {"ok": True, "test_id": test_id, "target_key": target_key, "config": config_content}
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
