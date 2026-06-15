"""ac-postrace-trigger — fire the post-race AI analysis when a session ends.

Consumes the ``test-completed`` topic, produced by ``ac-telemetry-lake`` when a
stream key goes silent (``_on_stream_timeout``). The event key is the
``session_id`` (the lake re-groups raw telemetry by session before the sink, so
the silence timeout fires per session, not per hostname).

For each event we POST ``{session_id, triggered_by: "auto"}`` to the Test
Manager backend, which resolves the owning test, dedups re-fires, and runs the
analysis under the admin PAT. Stateless; commits ~5s. Modeled on
``session-config-bridge``.

Consumes NEW events only: a fresh consumer group each start + ``latest`` means
a restart never drains an accumulated backlog and fires a burst of analyses
(see ``main()``). Skipped sessions can be analyzed manually.
"""

import logging
import os
import uuid

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
)
logger = logging.getLogger("ac_postrace_trigger")

# Test Manager backend — defaults to the in-cluster Kubernetes service name.
TEST_MANAGER_URL = os.environ.get("TEST_MANAGER_URL", "http://test-manager-backend")
ANALYSES_URL = f"{TEST_MANAGER_URL}/api/v1/analyses"

# Workspace service-account SDK token (auto-injected in-cluster). Passes the
# backend's update_permission. The AI-run token is chosen backend-side per F6
# (triggered_by=auto -> PAT_TOKEN), so this only authorizes the POST.
AUTH_TOKEN = os.environ.get("Quix__Sdk__Token", "")

# Shared secret proving this POST came from the trigger service (not a user
# crafting triggered_by="auto" to run under the admin PAT). Optional: when
# unset, the backend gate is a no-op. Set the SAME value on both deployments.
TRIGGER_SECRET = os.environ.get("TRIGGER_SECRET", "")


def _auth_headers() -> dict[str, str]:
    headers = {"authorization": AUTH_TOKEN} if AUTH_TOKEN else {}
    if TRIGGER_SECRET:
        headers["x-trigger-secret"] = TRIGGER_SECRET
    return headers


def _session_id_from(value: object, key: object) -> str:
    """Extract the session_id from a test-completed event.

    The event JSON carries ``key`` = session_id; fall back to the raw Kafka
    message key if the value isn't the expected dict.
    """
    if isinstance(value, dict) and value.get("key"):
        return str(value["key"])
    if isinstance(key, bytes):
        return key.decode()
    return str(key) if key else ""


def trigger_analysis(session_id: str, *, client: httpx.Client) -> None:
    """POST an auto analysis request for ``session_id``.

    A 404 means the session isn't linked to a test yet (a benign race — the
    bridge links at session start, the timeout fires ~10s after the last raw
    message, so the link normally exists). Treated as skip, not an error.
    """
    if not session_id:
        logger.warning("[trigger] empty session_id — skipping")
        return

    try:
        resp = client.post(
            ANALYSES_URL,
            json={"session_id": session_id, "triggered_by": "auto"},
            headers=_auth_headers(),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.error("[trigger] POST failed for session %s — %s", session_id, exc)
        return

    if resp.status_code in (200, 202):
        analysis_id = resp.json().get("analysis_id")
        logger.info(
            "[trigger] session %s → analysis %s (%d)",
            session_id,
            analysis_id,
            resp.status_code,
        )
    elif resp.status_code == 404:
        logger.info(
            "[trigger] session %s not linked to a test yet — skip (404)", session_id
        )
    else:
        logger.error(
            "[trigger] session %s → %d %s",
            session_id,
            resp.status_code,
            resp.text[:300],
        )


def process_event(value: object, key: object, timestamp: object, headers: object) -> None:
    """QuixStreams handler — one test-completed event in, one trigger POST out."""
    session_id = _session_id_from(value, key)
    logger.info("[trigger] test-completed event key=%s", session_id)
    with httpx.Client() as client:
        trigger_analysis(session_id, client=client)


def main() -> None:
    from quixstreams import Application

    if not AUTH_TOKEN:
        logger.warning(
            "[trigger] Quix__Sdk__Token unset — POSTs will be unauthenticated "
            "and rejected by the backend"
        )

    # Fresh consumer group each start (unless CONSUMER_GROUP is pinned). A new
    # group has no committed offset, so auto_offset_reset="latest" seeks to the
    # topic tail — we consume ONLY events produced after startup. Deliberate
    # safety choice: a restart must never drain a backlog of accumulated
    # test-completed events and fire many AI analyses at once (e.g. several
    # distinct sessions that ended while the service was down). Those can be
    # analyzed manually afterwards. Trade-off: a session-end during downtime is
    # skipped, by design.
    consumer_group = os.environ.get("CONSUMER_GROUP") or (
        f"ac_postrace_trigger_{uuid.uuid4().hex[:8]}"
    )
    app = Application(
        consumer_group=consumer_group,
        auto_offset_reset="latest",
        commit_interval=float(os.environ.get("COMMIT_INTERVAL", "5")),
    )
    logger.info(
        "[trigger] starting — consumer_group=%s (new events only; backlog skipped)",
        consumer_group,
    )
    input_topic = app.topic(
        name=os.environ.get("input", "test-completed"),
        value_deserializer="json",
    )
    sdf = app.dataframe(topic=input_topic)
    sdf = sdf.update(process_event, metadata=True)
    app.run()


if __name__ == "__main__":
    main()
