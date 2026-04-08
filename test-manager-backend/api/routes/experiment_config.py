"""
Experiment Config routes — proxy to Dynamic Configuration Manager.

Replicates the config-form logic as native API endpoints so the
frontend can submit experiment configs without an iframe.
"""

import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/experiment-config", tags=["experiment-config"])

CONFIG_TYPE = "experiment"
CATEGORY = "ac-telemetry"


class ExperimentConfigSubmit(BaseModel):
    rig_hostname: str = "*"
    environment: str = ""
    test_rig: str = ""
    experiment_id: str
    driver: str = ""
    requirements: str = ""


class ExperimentConfigResponse(BaseModel):
    ok: bool
    test_id: str
    target_key: str
    config: dict


class CurrentConfigsResponse(BaseModel):
    configs: dict


def _auth_headers(settings: Settings) -> dict:
    if settings.sdk_token:
        return {"Authorization": f"Bearer {settings.sdk_token}"}
    return {}


async def _find_config_id(api_base: str, target_key: str, headers: dict) -> str | None:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{api_base}/configurations",
            headers=headers,
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            configs = data if isinstance(data, list) else data.get("data", data.get("items", []))
            for cfg in configs:
                meta = cfg.get("metadata", {})
                if meta.get("type") == CONFIG_TYPE and meta.get("target_key") == target_key:
                    return cfg.get("id") or cfg.get("_id")
    return None


@router.get("/current", response_model=CurrentConfigsResponse)
async def get_current_configs(settings: Settings = Depends(get_settings)):
    """Fetch current experiment configs for all rigs."""
    api_base = f"{settings.config_api_url.rstrip('/')}/api/v1"
    headers = _auth_headers(settings)


    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{api_base}/configurations",
                headers=headers,
                timeout=5.0,
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="Could not fetch configs")

            data = resp.json()
            configs = data if isinstance(data, list) else data.get("data", data.get("items", []))

            results = {}
            for cfg in configs:
                meta = cfg.get("metadata", {})
                if meta.get("type") == CONFIG_TYPE:
                    config_id = cfg.get("id") or cfg.get("_id")
                    target_key = meta.get("target_key", "")
                    content_resp = await client.get(
                        f"{api_base}/configurations/{config_id}/content",
                        headers=headers,
                        timeout=5.0,
                    )
                    if content_resp.status_code == 200:
                        results[target_key] = content_resp.json()

            return {"configs": results}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to fetch current configs")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/submit", response_model=ExperimentConfigResponse)
async def submit_experiment_config(
    form_data: ExperimentConfigSubmit,
    settings: Settings = Depends(get_settings),
):
    """Create or update an experiment config in the Dynamic Configuration Manager."""
    api_base = f"{settings.config_api_url.rstrip('/')}/api/v1"
    headers = _auth_headers(settings)
    target_key = form_data.rig_hostname

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    test_id = f"run_{ts}_{form_data.driver}"

    config_content = {
        "test_id": test_id,
        "timestamp": ts,
        "environment": form_data.environment,
        "test_rig": form_data.test_rig,
        "experiment_id": form_data.experiment_id,
        "driver": form_data.driver,
        "requirements": form_data.requirements,
    }

    try:
        config_id = await _find_config_id(api_base, target_key, headers)

        async with httpx.AsyncClient() as client:
            if config_id:
                resp = await client.put(
                    f"{api_base}/configurations/{config_id}",
                    json={
                        "metadata": {
                            "category": CATEGORY,
                            "valid_from": ts,
                        },
                        "content": config_content,
                    },
                    headers=headers,
                    timeout=10.0,
                )
            else:
                resp = await client.post(
                    f"{api_base}/configurations",
                    json={
                        "metadata": {
                            "type": CONFIG_TYPE,
                            "target_key": target_key,
                            "category": CATEGORY,
                            "valid_from": ts,
                        },
                        "content": config_content,
                    },
                    headers=headers,
                    timeout=10.0,
                )

            if resp.status_code in (200, 201):
                logger.info("Config submitted: %s (target_key=%s)", test_id, target_key)
                return {"ok": True, "test_id": test_id, "target_key": target_key, "config": config_content}
            else:
                logger.error("Config Manager returned %d: %s", resp.status_code, resp.text)
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to submit config")
        raise HTTPException(status_code=500, detail=str(e))
