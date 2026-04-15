"""
Session Config Bridge — Consumes AC session data from Kafka and pushes it
to the Dynamic Configuration Manager so it can be used with join_lookup.

Each session message (one per AC session change) becomes a config version
with type="session" and target_key=<hostname>.

Also links the session to the currently-active Test in Test Manager by
calling POST /tests/{test_id}/sessions on the test-manager backend.
"""

import json
import logging
import os
import socket
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

# Test Manager backend — defaults to in-cluster Kubernetes service name.
TEST_MANAGER_URL = os.environ.get("TEST_MANAGER_URL", "http://test-manager-backend")
TEST_MANAGER_API = f"{TEST_MANAGER_URL}/api/v1"


def _auth_headers() -> dict:
    if AUTH_TOKEN:
        return {"authorization": AUTH_TOKEN}
    return {}


def _find_config_id(target_key: str) -> str | None:
    """Search for existing config by type and target key."""
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
                    return cfg.get("id") or cfg.get("_id")
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
            logger.info(
                "Session config pushed: target_key=%s car=%s track=%s (id=%s)",
                target_key, content.get("carModel"), content.get("track"), config_id or "new",
            )
        else:
            logger.error(
                "Config Manager returned %d: %s", resp.status_code, resp.text[:300],
            )


def _find_experiment_config_id(target_key: str) -> str | None:
    """Find the experiment config in DCM for this hostname."""
    with httpx.Client() as client:
        resp = client.get(f"{API_BASE}/configurations", headers=_auth_headers(), timeout=5.0)
        if resp.status_code != 200:
            logger.warning("DCM list returned %d when looking for experiment config", resp.status_code)
            return None
        data = resp.json()
        configs = data if isinstance(data, list) else data.get("data", data.get("items", []))
        for cfg in configs:
            meta = cfg.get("metadata", {})
            if meta.get("type") == "experiment" and meta.get("target_key") == target_key:
                return cfg.get("id") or cfg.get("_id")
    return None


def _get_current_test_id(target_key: str) -> str | None:
    """Resolve the test_id of the latest experiment config version for this hostname."""
    config_id = _find_experiment_config_id(target_key)
    if not config_id:
        logger.info("[link] No experiment config found for hostname=%s", target_key)
        return None

    with httpx.Client() as client:
        resp = client.get(
            f"{API_BASE}/configurations/{config_id}/versions",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if resp.status_code != 200:
            logger.warning("[link] DCM versions list returned %d for config=%s", resp.status_code, config_id)
            return None
        versions = resp.json()
        if isinstance(versions, dict):
            versions = versions.get("data", versions.get("items", []))
        if not versions:
            logger.info("[link] Experiment config %s has no versions", config_id)
            return None

        # Pick latest by version number
        latest = max(versions, key=lambda v: (v.get("metadata", v).get("version") or 0))
        version = latest.get("metadata", latest).get("version")

        content_resp = client.get(
            f"{API_BASE}/configurations/{config_id}/versions/{version}/content",
            headers=_auth_headers(),
            timeout=5.0,
        )
        if content_resp.status_code != 200:
            logger.warning("[link] DCM content fetch returned %d for v%s", content_resp.status_code, version)
            return None
        content = content_resp.json()
        test_id = content.get("test_id")
        logger.info("[link] hostname=%s → config=%s v%s → test_id=%s", target_key, config_id, version, test_id)
        return test_id


def _link_session_to_test(target_key: str, value: dict) -> None:
    """POST the session to the Test Manager backend, linking it to the active test."""
    test_id = _get_current_test_id(target_key)
    if not test_id:
        logger.info("[link] Skipping link — no test_id resolved for hostname=%s", target_key)
        return

    payload = {
        "session_id": value.get("sessionId") or value.get("session_id"),
        "track": value.get("track"),
        "car_model": value.get("carModel") or value.get("car_model"),
    }
    if not payload["session_id"]:
        logger.warning("[link] Session message has no session_id, skipping link: %s", value)
        return

    url = f"{TEST_MANAGER_API}/tests/{test_id}/sessions"
    logger.info("[link] POST %s payload=%s", url, payload)

    try:
        with httpx.Client() as client:
            resp = client.post(url, json=payload, headers=_auth_headers(), timeout=10.0)
    except httpx.ConnectError as e:
        logger.error("[link] CONNECT FAILED to %s — %s (DNS or network issue)", TEST_MANAGER_URL, e)
        return
    except httpx.HTTPError as e:
        logger.error("[link] HTTP error to %s — %s", url, e)
        return

    if resp.status_code in (200, 201):
        logger.info("[link] OK %d — session %s linked to %s", resp.status_code, payload["session_id"], test_id)
    else:
        logger.error("[link] FAILED %d — body=%s", resp.status_code, resp.text[:300])


def _probe_test_manager() -> None:
    """One-shot startup probe to verify the in-cluster URL is reachable."""
    logger.info("[probe] Test Manager URL configured: %s", TEST_MANAGER_URL)

    # DNS check
    try:
        host = TEST_MANAGER_URL.split("://", 1)[-1].split("/")[0].split(":")[0]
        ip = socket.gethostbyname(host)
        logger.info("[probe] DNS %s → %s", host, ip)
    except Exception as e:
        logger.error("[probe] DNS FAILED for %s — %s", TEST_MANAGER_URL, e)
        return

    # /health check (no auth required)
    try:
        with httpx.Client() as client:
            resp = client.get(f"{TEST_MANAGER_URL}/health", timeout=5.0)
        logger.info("[probe] GET /health → %d %s", resp.status_code, resp.text[:200])
    except Exception as e:
        logger.error("[probe] /health FAILED — %s", e)


def process_session(value: dict, key, timestamp, headers):
    """Called for each session message from Kafka."""
    # The Kafka key is the hostname
    target_key = key.decode() if isinstance(key, bytes) else str(key)
    logger.info("Received session for %s: car=%s track=%s", target_key, value.get("carModel"), value.get("track"))
    _push_to_config_manager(target_key, value)
    _link_session_to_test(target_key, value)
    return value


def main():
    _probe_test_manager()

    app = Application(consumer_group="session_config_bridge", auto_offset_reset="earliest")
    input_topic = app.topic(name=os.environ.get("input", "ac-telemetry-session"))

    sdf = app.dataframe(topic=input_topic)
    sdf = sdf.update(process_session, metadata=True)

    app.run()


if __name__ == "__main__":
    main()
