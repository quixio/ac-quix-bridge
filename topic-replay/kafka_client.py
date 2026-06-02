"""Direct confluent_kafka Consumer/Producer factory with two modes.

**Cloud mode (default — `BROKER_ADDRESS` unset).** Pulls bootstrap server +
CA cert from the Quix portal using `Quix_Pat_Token`, and attaches the
broader `Sasl__Username` / `Sasl__Password` credentials provisioned with
read+write ACLs. Topic names are workspace-prefixed.

Why this exists instead of `quixstreams.Application`: the portal-returned
librdkafka config embeds a SASL principal that is producer-only for these
topics, so any consumer subscription bounces with `TOPIC_AUTHORIZATION_FAILED`.
We bypass that by pulling only the bootstrap + CA cert from the portal,
then attaching wider SASL creds with both read and write ACLs.

**Local mode (`BROKER_ADDRESS` env var set & non-empty).** Skips the portal
fetch entirely and connects to a plaintext local broker at `BROKER_ADDRESS`
(typically `localhost:29092` from the host, or `kafka:9092` from another
container in the compose network). No SASL, no SSL, no workspace prefix.

The local-mode predicate is the **single** switch: cloud-mode code paths
are byte-identical to the prior Phase-1.5 implementation when
`BROKER_ADDRESS` is unset. See `dev-planning/topic-replay/spec-local.md`.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
import time

import httpx
from confluent_kafka import Consumer, Producer

logger = logging.getLogger(__name__)


def _is_local_mode() -> bool:
    """True when `BROKER_ADDRESS` is set and non-empty."""
    return bool(os.environ.get("BROKER_ADDRESS", "").strip())


def _required_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"{name} not set. Topic-replay needs SASL creds in .env "
            "(Sasl__Username / Sasl__Password) and a Quix_Pat_Token to "
            "fetch broker config."
        )
    return val


def _fetch_broker_config() -> dict:
    """Fetch `bootstrap.servers` + `ssl.ca.cert` (base64 PEM) from the Quix
    portal using `Quix_Pat_Token`. SASL creds in the response are ignored —
    we use the env-provided ones, which carry the right ACLs."""
    pat = _required_env("Quix_Pat_Token")
    ws = _required_env("Quix__Workspace__Id")
    portal = _required_env("Quix__Portal__Api")
    resp = httpx.get(
        f"{portal}/workspaces/{ws}/broker/librdkafka",
        headers={"Authorization": f"bearer {pat}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _write_ca_cert(cfg: dict) -> str:
    """Drop the base64 CA cert from the portal config into a temp file and
    return its path. The Consumer/Producer reference this path via
    `ssl.ca.location`."""
    ca_b64 = cfg.get("ssl.ca.cert")
    if not ca_b64:
        raise RuntimeError("Portal librdkafka config missing ssl.ca.cert")
    fd = tempfile.NamedTemporaryFile("wb", delete=False, suffix=".pem")
    fd.write(base64.b64decode(ca_b64))
    fd.close()
    return fd.name


def _local_config() -> dict:
    """Plaintext local-broker config — no SASL, no SSL.

    Reads `BROKER_ADDRESS` verbatim; caller has already gated this through
    `_is_local_mode()` so the env var is guaranteed non-empty.
    """
    return {"bootstrap.servers": os.environ["BROKER_ADDRESS"].strip()}


def _cloud_config() -> dict:
    """Shared SASL_SSL config block for Consumer and Producer (cloud)."""
    cfg = _fetch_broker_config()
    ca_path = _write_ca_cert(cfg)
    return {
        "bootstrap.servers": cfg["bootstrap.servers"],
        "security.protocol": "SASL_SSL",
        "sasl.mechanism": "SCRAM-SHA-256",
        "sasl.username": _required_env("Sasl__Username"),
        "sasl.password": _required_env("Sasl__Password"),
        "ssl.ca.location": ca_path,
    }


def _base_config() -> dict:
    """Dispatch to the right config builder based on env mode."""
    if _is_local_mode():
        return _local_config()
    return _cloud_config()


def workspace_id() -> str:
    """Return the Quix workspace ID (cloud mode only).

    In **local mode** there is no workspace, and topic names are not
    prefixed; calling this is a programming error. We raise to force callers
    to route through `scoped_consumer_group()` / `to_full_topic()` instead.
    """
    if _is_local_mode():
        raise RuntimeError(
            "workspace_id() is not available in local mode "
            "(BROKER_ADDRESS is set). Use scoped_consumer_group()/"
            "to_full_topic() helpers, which already account for mode."
        )
    return _required_env("Quix__Workspace__Id")


def to_full_topic(short: str) -> str:
    """Prepend the workspace ID to a short topic name (cloud mode).

    In local mode, returns `short` verbatim — the local broker has no
    workspace namespace.
    """
    if _is_local_mode():
        return short
    return f"{workspace_id()}-{short}"


def scoped_consumer_group(short: str) -> str:
    """Return a consumer-group name scoped to the current mode.

    Cloud mode prepends the workspace ID (`<ws>-<short>`). Local mode
    returns `short` verbatim — no namespace.
    """
    if _is_local_mode():
        return short
    return f"{workspace_id()}-{short}"


def make_consumer(
    short_topics: list[str],
    consumer_group: str | None = None,
    offset: str = "latest",
) -> tuple[Consumer, dict[str, str]]:
    """Create a Consumer subscribed to `short_topics`.

    In cloud mode the topic names are prefixed with the workspace ID before
    subscription; in local mode the short names are used verbatim. Returns
    `(consumer, full_to_short)` — the map lets the caller recover the short
    name from `msg.topic()` for JSONL filename routing.
    """
    cfg = _base_config()
    group = consumer_group or scoped_consumer_group(
        f"topic-replay-{int(time.time())}"
    )
    cfg.update(
        {
            "group.id": group,
            "auto.offset.reset": offset,
            "enable.auto.commit": False,
        }
    )
    consumer = Consumer(cfg)
    full = [to_full_topic(s) for s in short_topics]
    consumer.subscribe(full)
    logger.info(
        "Consumer subscribed: %s (group=%s, offset=%s, mode=%s)",
        full,
        group,
        offset,
        "local" if _is_local_mode() else "cloud",
    )
    return consumer, dict(zip(full, short_topics))


def make_producer() -> Producer:
    """Create a Producer with the active-mode config block."""
    return Producer(_base_config())
