"""
Session Config Bridge — Consumes AC session data from Kafka and pushes it
to the Dynamic Configuration Manager so it can be used with join_lookup.

Each session message (one per AC session change) becomes a config version
with type="session" and target_key=<hostname>.
"""

import json
import logging
import os
from datetime import datetime, timezone

import httpx
from quixstreams import Application

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger(__name__)

CONFIG_MANAGER_URL = os.environ.get("CONFIG_MANAGER_URL", "https://config-api-svc-quixers-acquixbridge-dev.az-france-0.app.quix.io")
CONFIG_TYPE = os.environ.get("CONFIG_TYPE", "session")
API_BASE = f"{CONFIG_MANAGER_URL}/api/v1"
AUTH_TOKEN = os.environ.get("Quix__Sdk__Token", "")

# Cache of known config IDs per target_key (hostname)
_config_ids: dict[str, str] = {}


def _auth_headers() -> dict:
    if AUTH_TOKEN:
        return {"authorization": AUTH_TOKEN}
    return {}


def _find_config_id(target_key: str) -> str | None:
    """Search for existing config by type and target key."""
    if target_key in _config_ids:
        return _config_ids[target_key]

    with httpx.Client() as client:
        resp = client.get(
            f"{API_BASE}/configurations",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            configs = data if isinstance(data, list) else data.get("data", data.get("items", []))
            for cfg in configs:
                meta = cfg.get("metadata", {})
                if meta.get("type") == CONFIG_TYPE and meta.get("target_key") == target_key:
                    config_id = cfg.get("id") or cfg.get("_id")
                    _config_ids[target_key] = config_id
                    return config_id
    return None


def _push_to_config_manager(target_key: str, content: dict):
    """Create or update a session config in the Dynamic Configuration Manager."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    config_id = _find_config_id(target_key)

    with httpx.Client() as client:
        if config_id:
            resp = client.put(
                f"{API_BASE}/configurations/{config_id}",
                json={
                    "metadata": {
                        "category": "ac-telemetry",
                        "valid_from": ts,
                    },
                    "content": content,
                },
                headers=_auth_headers(),
                timeout=10.0,
            )
        else:
            resp = client.post(
                f"{API_BASE}/configurations",
                json={
                    "metadata": {
                        "type": CONFIG_TYPE,
                        "target_key": target_key,
                        "category": "ac-telemetry",
                        "valid_from": ts,
                    },
                    "content": content,
                },
                headers=_auth_headers(),
                timeout=10.0,
            )
            if resp.status_code in (200, 201):
                # Cache the new config ID
                result = resp.json()
                new_id = result.get("id") or result.get("_id")
                if new_id:
                    _config_ids[target_key] = new_id

        if resp.status_code in (200, 201):
            logger.info(
                "Session config pushed: target_key=%s car=%s track=%s (id=%s)",
                target_key, content.get("carModel"), content.get("track"), config_id or "new",
            )
        else:
            logger.error(
                "Config Manager returned %d: %s", resp.status_code, resp.text[:300],
            )


def process_session(value: dict, key, timestamp, headers):
    """Called for each session message from Kafka."""
    # The Kafka key is the hostname
    target_key = key.decode() if isinstance(key, bytes) else str(key)
    logger.info("Received session for %s: car=%s track=%s", target_key, value.get("carModel"), value.get("track"))
    _push_to_config_manager(target_key, value)
    return value


def main():
    app = Application(consumer_group="session_config_bridge", auto_offset_reset="earliest")
    input_topic = app.topic(name=os.environ.get("input", "ac-telemetry-session"))

    sdf = app.dataframe(topic=input_topic)
    sdf = sdf.update(process_session, metadata=True)

    app.run()


if __name__ == "__main__":
    main()
