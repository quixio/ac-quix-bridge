"""
Telemetry Dashboard — FastAPI + QuixStreams consumer + WebSocket broadcast.

Consumes telemetry from a Kafka topic and pushes it to browser clients
over WebSocket for real-time visualization.
"""
import os
print(os.environ)


import asyncio
import csv
import io
import json
import logging
import re
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

# Consumer health, surfaced to the UI and /health. One of:
# "starting" | "connecting" | "connected" | "reconnecting".
consumer_state: dict[str, str | None] = {"status": "starting", "detail": None}

# Current driver name, resolved from ac-telemetry-config events via the DCM.
current_driver: dict[str, str | None] = {"name": None}

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
        r = httpx.get(url, headers=headers, timeout=10, verify=_datalake_verify())
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global loop, kafka_thread, config_thread
    loop = asyncio.get_event_loop()
    kafka_thread = threading.Thread(target=run_kafka, daemon=True)
    kafka_thread.start()
    config_thread = threading.Thread(target=run_config_consumer, daemon=True)
    config_thread.start()
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


# --- Leaderboard: all-time fastest-lap-per-driver. Default source is the
# best-laps-cache service (GET {BEST_LAPS_CACHE_URL}/best-laps, in-cluster
# http://best-laps-cache). When BEST_LAPS_CACHE_URL is unset/empty we fall back
# to proxying a Data Lake /query call (legacy path): token stays server-side
# (env var), the full SQL is an env var so the table/columns can be tuned without
# a code change, defaulting to the lake table named by TABLE_NAME (per-environment)
# with `driver` / `iBestTime` columns.
_LEADERBOARD_TABLE = os.environ.get("TABLE_NAME", "ac_telemetry")
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", _LEADERBOARD_TABLE):
    raise ValueError(f"TABLE_NAME must be a bare SQL identifier, got {_LEADERBOARD_TABLE!r}")
DEFAULT_LEADERBOARD_SQL = (
    'SELECT driver AS name, MIN("iBestTime") AS ms '
    f"FROM {_LEADERBOARD_TABLE} "
    "WHERE \"iBestTime\" > 0 AND driver IS NOT NULL AND driver <> '' "
    "GROUP BY driver ORDER BY ms ASC LIMIT 100"
)
_lb_cache: dict = {"ts": 0.0, "rows": []}


def _datalake_verify():
    """TLS verification for the lake call. Honour the platform CA bundle when set
    (in-cluster REQUESTS_CA_BUNDLE / SSL_CERT_FILE); allow an explicit insecure
    override (DATALAKE_INSECURE_SSL=true) for self-signed internal/edge certs."""
    if os.environ.get("DATALAKE_INSECURE_SSL", "").lower() in ("1", "true", "yes"):
        return False
    return os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True


def _parse_leaderboard_csv(text: str) -> list[dict]:
    """Parse the streamed CSV (columns: name, ms) into [{name, ms}], skipping a
    trailing `# ERROR: ...` line if the stream failed mid-flight."""
    rows: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        name = (row.get("name") or "").strip()
        if not name or name.startswith("# ERROR"):
            continue
        try:
            ms = int(float(row["ms"]))
        except (TypeError, ValueError, KeyError):
            continue
        rows.append({"name": name, "ms": ms})
    return rows


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


async def _fetch_from_cache(base_url: str) -> list[dict]:
    """GET the best-laps-cache /best-laps CSV and map it to the leaderboard
    shape. Filters: none (all-time leaderboard across all groups)."""
    target = f"{base_url.rstrip('/')}/best-laps"
    logger.info("Leaderboard: GET %s (filters: none)", target)
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=20, verify=_datalake_verify()) as client:
        r = await client.get(target)
    r.raise_for_status()
    rows = _parse_cache_csv(r.text)
    logger.info(
        "Leaderboard served from cache: %d rows in %.0fms",
        len(rows),
        (time.monotonic() - t0) * 1000,
    )
    return rows


async def _fetch_from_quixlake() -> list[dict]:
    """Fallback: query the Data Lake /query API directly (legacy path)."""
    url = os.environ.get("DATALAKE_API_URL") or os.environ.get("Quix__Lakehouse__Query__Url")
    token = os.environ.get("DATALAKE_API_TOKEN") or os.environ.get("Quix__Lakehouse__Query__AuthToken")
    if not url or not token:
        raise RuntimeError("datalake not configured")

    sql = os.environ.get("LEADERBOARD_SQL") or DEFAULT_LEADERBOARD_SQL
    logger.info("Leaderboard: POST %s/query (quixlake fallback)", url.rstrip("/"))
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=20, verify=_datalake_verify()) as client:
        r = await client.post(
            f"{url.rstrip('/')}/query",
            content=sql.encode("utf-8"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
        )
    r.raise_for_status()
    rows = _parse_leaderboard_csv(r.text)
    logger.info(
        "Leaderboard served from quixlake-fallback: %d rows in %.0fms",
        len(rows),
        (time.monotonic() - t0) * 1000,
    )
    return rows


@api.get("/leaderboard")
async def leaderboard():
    """All-time fastest lap per driver.

    Default source is the best-laps-cache service (BEST_LAPS_CACHE_URL); when
    that var is unset/empty we fall back to the legacy Data Lake /query path.
    Results are TTL-cached (LEADERBOARD_TTL_SECONDS).
    """
    ttl = float(os.environ.get("LEADERBOARD_TTL_SECONDS", "15"))
    now = time.monotonic()
    if _lb_cache["rows"] and now - _lb_cache["ts"] < ttl:
        return {"rows": _lb_cache["rows"], "cached": True}

    cache_url = os.environ.get("BEST_LAPS_CACHE_URL", "").strip()
    source = "cache" if cache_url else "quixlake-fallback"
    try:
        if cache_url:
            rows = await _fetch_from_cache(cache_url)
        else:
            rows = await _fetch_from_quixlake()
        _lb_cache.update(ts=now, rows=rows)
        return {"rows": rows, "source": source}
    except Exception as e:
        logger.exception("Leaderboard query failed (source=%s)", source)
        # Serve stale cache if we have one; otherwise surface the error.
        if _lb_cache["rows"]:
            return {"rows": _lb_cache["rows"], "stale": True, "source": source, "error": str(e)}
        return {"rows": [], "source": source, "error": str(e)}


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
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
        logger.info("WebSocket client disconnected (%d remaining)", len(clients))
