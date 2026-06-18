"""
Telemetry Dashboard — FastAPI + QuixStreams consumer + WebSocket broadcast.

Consumes telemetry from a Kafka topic and pushes it to browser clients
over WebSocket for real-time visualization.
"""
import asyncio
import csv
import io
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

clients: set[WebSocket] = set()
loop: asyncio.AbstractEventLoop | None = None
kafka_thread: threading.Thread | None = None
config_thread: threading.Thread | None = None
session_thread: threading.Thread | None = None

# Consumer health, surfaced to the UI and /health. One of:
# "starting" | "connecting" | "connected" | "reconnecting".
consumer_state: dict[str, str | None] = {"status": "starting", "detail": None}

# Current driver name, resolved from ac-telemetry-config events via the DCM.
current_driver: dict[str, str | None] = {"name": None}

# Current session's track + car, resolved from the latest ac-telemetry-session
# message. Drives the leaderboard filter and the header combo. None = unknown
# (no session seen yet) → leaderboard serves the unfiltered all-time board.
current_session: dict[str, str | None] = {"track": None, "carModel": None}

STATIC_DIR = Path(__file__).parent / "static"


def push_to_clients(value: dict):
    """Called from the Kafka consumer thread; schedules async sends on the main loop."""
    if loop is None or not clients:
        return
    data = json.dumps(value)
    asyncio.run_coroutine_threadsafe(_broadcast(data), loop)


def set_consumer_status(status: str, detail: str | None = None):
    """Update consumer health and push it to connected browsers.

    Called from the Kafka thread, so the broadcast is marshalled onto the loop.
    """
    consumer_state["status"] = status
    consumer_state["detail"] = detail
    logger.info("Consumer status: %s%s", status, f" ({detail})" if detail else "")
    if loop is None or not clients:
        return
    msg = json.dumps({"type": "status", "status": status, "detail": detail})
    asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)


def set_current_driver(name: str | None):
    """Update the current driver and push it to connected browsers."""
    if not name or name == current_driver["name"]:
        return
    current_driver["name"] = name
    logger.info("Current driver: %s", name)
    if loop is None or not clients:
        return
    msg = json.dumps({"type": "driver", "name": name})
    asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)


def set_current_session(track: str | None, carModel: str | None):
    """Update the current session's track + car and push it to browsers.

    Treats empty string as unknown (None). No-op when nothing changed or when
    both fields are unknown — mirrors set_current_driver's guard.
    """
    track = track or None
    carModel = carModel or None
    if not track and not carModel:
        return
    if track == current_session["track"] and carModel == current_session["carModel"]:
        return
    current_session["track"] = track
    current_session["carModel"] = carModel
    logger.info("Current session: track=%s carModel=%s", track, carModel)
    if loop is None or not clients:
        return
    msg = json.dumps({"type": "session", "track": track, "carModel": carModel})
    asyncio.run_coroutine_threadsafe(_broadcast(msg), loop)


async def _broadcast(data: str):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def run_kafka():
    """Run a raw Kafka consumer in a background thread (no signal handlers needed).

    Wrapped in a reconnect loop so transient failures — a 503 from the Quix
    portal API while building the app, a broker hiccup mid-stream — back off and
    retry instead of killing the thread permanently. Backoff resets on every
    successful connect.
    """
    from quixstreams import Application as QuixApp

    backoff = 1.0
    max_backoff = 30.0

    while True:
        consumer = None
        try:
            set_consumer_status("connecting")

            qx = QuixApp(consumer_group="telemetry-dashboard")
            topic_name = os.environ.get("input", "ac-telemetry-raw")
            topic = qx.topic(topic_name)

            real_topic_name = topic.name
            logger.info("Starting Kafka consumer on topic '%s' (real: '%s')", topic_name, real_topic_name)

            consumer = qx.get_consumer()
            consumer.subscribe([real_topic_name])

            set_consumer_status("connected")
            backoff = 1.0  # connected cleanly — reset backoff

            while True:
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.error("Consumer error: %s", msg.error())
                    continue

                value = json.loads(msg.value())
                push_to_clients(value)

        except Exception:
            logger.exception("Kafka consumer failed — retrying in %.0fs", backoff)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass

        set_consumer_status("reconnecting", f"retry in {int(backoff)}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


def _fetch_config_driver(config_id, content_url, version):
    """GET the DCM config content and return its `driver`. Prefers a configured
    CONFIG_MANAGER_URL base; otherwise uses the contentUrl carried in the event.
    Auth is the injected Quix__Sdk__Token (same as session-config-bridge)."""
    base = os.environ.get("CONFIG_MANAGER_URL", "").rstrip("/")
    if base and config_id and version is not None:
        url = f"{base}/api/v1/configurations/{config_id}/versions/{version}/content"
    else:
        url = content_url
    if not url:
        return None
    token = os.environ.get("Quix__Sdk__Token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = httpx.get(url, headers=headers, timeout=10, verify=_http_verify())
        r.raise_for_status()
        return r.json().get("driver")
    except Exception:
        logger.exception("Driver lookup failed (config=%s v=%s)", config_id, version)
        return None


def _handle_config_event(data: dict):
    """Resolve + broadcast the driver from an ac-telemetry-config event."""
    meta = data.get("metadata") or {}
    if meta.get("type") != "experiment" or data.get("event") == "deleted":
        return
    driver = _fetch_config_driver(data.get("id"), data.get("contentUrl"), meta.get("version"))
    set_current_driver(driver)


def run_config_consumer():
    """Consume ac-telemetry-config; on each experiment config event resolve the
    current driver from the DCM and broadcast it. Reads from earliest with
    auto-commit off, so the current driver is recovered on every startup
    (latest event wins). Reconnect loop mirrors run_kafka.
    """
    from quixstreams import Application as QuixApp

    backoff = 1.0
    max_backoff = 30.0

    while True:
        consumer = None
        try:
            qx = QuixApp(
                consumer_group="telemetry-dashboard-config",
                auto_offset_reset="earliest",
                consumer_extra_config={"enable.auto.commit": False},
            )
            topic_name = os.environ.get("config_input", "ac-telemetry-config")
            topic = qx.topic(topic_name)
            consumer = qx.get_consumer()
            consumer.subscribe([topic.name])
            logger.info("Config consumer on '%s' (real: '%s')", topic_name, topic.name)
            backoff = 1.0

            while True:
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.error("Config consumer error: %s", msg.error())
                    continue
                try:
                    _handle_config_event(json.loads(msg.value()))
                except Exception:
                    logger.exception("Failed handling config event")

        except Exception:
            logger.exception("Config consumer failed — retrying in %.0fs", backoff)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass

        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


def run_session_consumer():
    """Consume ac-telemetry-session; on each message store the current track +
    carModel and broadcast them. Reads from earliest with auto-commit off, so
    the current combo is recovered on every startup (latest message wins).
    Reconnect loop mirrors run_config_consumer.
    """
    from quixstreams import Application as QuixApp

    backoff = 1.0
    max_backoff = 30.0

    while True:
        consumer = None
        try:
            qx = QuixApp(
                consumer_group="telemetry-dashboard-session",
                auto_offset_reset="earliest",
                consumer_extra_config={"enable.auto.commit": False},
            )
            topic_name = os.environ.get("session_input", "ac-telemetry-session")
            topic = qx.topic(topic_name)
            consumer = qx.get_consumer()
            consumer.subscribe([topic.name])
            logger.info("Session consumer on '%s' (real: '%s')", topic_name, topic.name)
            backoff = 1.0

            while True:
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    logger.error("Session consumer error: %s", msg.error())
                    continue
                try:
                    data = json.loads(msg.value())
                    set_current_session(data.get("track"), data.get("carModel"))
                except Exception:
                    logger.exception("Failed handling session message")

        except Exception:
            logger.exception("Session consumer failed — retrying in %.0fs", backoff)
        finally:
            if consumer is not None:
                try:
                    consumer.close()
                except Exception:
                    pass

        time.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, kafka_thread, config_thread, session_thread
    loop = asyncio.get_event_loop()
    kafka_thread = threading.Thread(target=run_kafka, daemon=True)
    kafka_thread.start()
    config_thread = threading.Thread(target=run_config_consumer, daemon=True)
    config_thread.start()
    session_thread = threading.Thread(target=run_session_consumer, daemon=True)
    session_thread.start()
    logger.info("Dashboard started — open http://localhost:8000")
    yield
    logger.info("Shutting down dashboard")


api = FastAPI(lifespan=lifespan)


@api.get("/health")
async def health():
    """Liveness + consumer health. Registered before the catch-all route."""
    return {
        "status": consumer_state["status"],
        "detail": consumer_state["detail"],
        "clients": len(clients),
    }


# --- Leaderboard: all-time fastest-lap-per-driver. The sole source is the
# best-laps-cache service (GET {BEST_LAPS_CACHE_URL}/best-laps, in-cluster
# http://best-laps-cache). Results are TTL-cached in-process. There is no
# lakehouse fallback: if the cache is unavailable we serve stale rows when we
# have them and otherwise an empty board. The live current-driver overlay is a
# separate path (the /ws topic feed), not part of these standings.
# Keyed by (track, carModel) so a session switch never serves the previous
# combo's stale rows. (None, None) is the unfiltered cold-start board.
_lb_cache: dict[tuple[str | None, str | None], dict] = {}


def _http_verify():
    """TLS verification for outbound HTTP (best-laps-cache + DCM driver lookup).
    Honour the platform CA bundle when set (in-cluster REQUESTS_CA_BUNDLE /
    SSL_CERT_FILE); otherwise verify normally."""
    return os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True


def _parse_cache_csv(text: str) -> list[dict]:
    """Parse the best-laps-cache CSV (columns: environment, experiment, track,
    carModel, driver, iBestTime) into the leaderboard's [{name, ms}] shape.

    The cache returns one row per (group, driver); the leaderboard renders one
    row per driver, so we reduce to the fastest iBestTime per driver here. Maps
    `driver` -> `name` and `iBestTime` -> `ms`.
    """
    by_driver: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("driver") or "").strip()
        if not name:
            continue
        try:
            ms = int(float(row["iBestTime"]))
        except (TypeError, ValueError, KeyError):
            continue
        if ms <= 0:
            continue
        cur = by_driver.get(name)
        if cur is None or ms < cur:
            by_driver[name] = ms
    rows = [{"name": name, "ms": ms} for name, ms in by_driver.items()]
    rows.sort(key=lambda r: r["ms"])
    return rows


async def _fetch_from_cache(
    base_url: str, track: str | None = None, carModel: str | None = None
) -> list[dict]:
    """GET the best-laps-cache /best-laps CSV and map it to the leaderboard
    shape. When track/carModel are given they are passed as query params so the
    cache filters server-side (param name `carModel` matches the cache
    contract). Empty/None filters are dropped → unfiltered all-time board."""
    target = f"{base_url.rstrip('/')}/best-laps"
    params = {k: v for k, v in {"track": track, "carModel": carModel}.items() if v}
    logger.info("Leaderboard: GET %s (filters=%s)", target, params or "none")
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=20, verify=_http_verify()) as client:
        r = await client.get(target, params=params)
    r.raise_for_status()
    rows = _parse_cache_csv(r.text)
    logger.info(
        "Leaderboard served from cache: %d rows in %.0fms",
        len(rows),
        (time.monotonic() - t0) * 1000,
    )
    return rows


@api.get("/leaderboard")
async def leaderboard():
    """All-time fastest lap per driver, sourced solely from best-laps-cache.

    Source is the best-laps-cache service (BEST_LAPS_CACHE_URL). Results are
    TTL-cached (LEADERBOARD_TTL_SECONDS). There is no lakehouse fallback: on a
    fetch error we serve the last good (stale) rows when present, otherwise an
    empty board. A blank BEST_LAPS_CACHE_URL is a misconfiguration, not a
    fallback trigger.
    """
    # Filter by the current session's combo. Before any session message arrives
    # both are None → unfiltered all-time board under cache key (None, None).
    track = current_session["track"]
    carModel = current_session["carModel"]
    key = (track, carModel)

    ttl = float(os.environ.get("LEADERBOARD_TTL_SECONDS", "15"))
    now = time.monotonic()
    entry = _lb_cache.get(key)
    if entry and entry["rows"] and now - entry["ts"] < ttl:
        return {"rows": entry["rows"], "cached": True}

    cache_url = os.environ.get("BEST_LAPS_CACHE_URL", "").strip()
    if not cache_url:
        logger.warning("Leaderboard: BEST_LAPS_CACHE_URL not configured — serving empty board")
        return {"rows": [], "error": "BEST_LAPS_CACHE_URL not configured"}

    try:
        rows = await _fetch_from_cache(cache_url, track, carModel)
        _lb_cache[key] = {"ts": now, "rows": rows}
        return {"rows": rows, "source": "cache"}
    except Exception as e:
        logger.exception("Leaderboard cache fetch failed")
        # Stale-then-empty: serve last good rows for this combo if we have any.
        if entry and entry["rows"]:
            return {"rows": entry["rows"], "stale": True, "source": "cache", "error": str(e)}
        return {"rows": [], "source": "cache", "error": str(e)}


@api.get("/{full_path:path}")
async def root(full_path: str = ""):
    # Serve real static assets (e.g. steering-wheel.png) when the path maps to an
    # existing file under STATIC_DIR; otherwise fall through to the SPA index.
    if full_path:
        try:
            candidate = (STATIC_DIR / full_path).resolve()
            if candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
                return FileResponse(str(candidate))
        except (OSError, ValueError):
            pass

    index = STATIC_DIR / "index.html"
    logger.info("Request for '/%s' — serving %s (exists: %s)", full_path, index, index.exists())
    if not index.exists():
        # Fallback: try /app/static directly
        fallback = Path("/app/static/index.html")
        logger.info("Trying fallback %s (exists: %s)", fallback, fallback.exists())
        if fallback.exists():
            return FileResponse(str(fallback))
        # List what's actually in /app for debugging
        app_contents = list(Path("/app").rglob("*"))
        logger.info("Contents of /app: %s", app_contents)
        return {"error": "index.html not found", "static_dir": str(STATIC_DIR), "app_contents": [str(p) for p in app_contents]}
    return FileResponse(str(index))


@api.websocket("/ws")
async def ws_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    logger.info("WebSocket client connected (%d total)", len(clients))
    # Send current consumer health immediately so a freshly-loaded page reflects
    # reality instead of assuming "live" just because the socket opened.
    await websocket.send_text(
        json.dumps({"type": "status", "status": consumer_state["status"], "detail": consumer_state["detail"]})
    )
    if current_driver["name"]:
        await websocket.send_text(json.dumps({"type": "driver", "name": current_driver["name"]}))
    if current_session["track"] or current_session["carModel"]:
        await websocket.send_text(json.dumps({"type": "session", **current_session}))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(clients))
