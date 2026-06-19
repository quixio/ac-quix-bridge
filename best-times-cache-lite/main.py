"""best-times-cache-lite — QuixStreams-native best-lap cache.

Three-SDF pipeline (session + config + raw telemetry) feeding a module-level
``_board`` dict that the FastAPI HTTP server reads directly — no request bridge,
no PendingRequests round-trip.  RocksDB state (via ``stateful=True``) persists
best laps across restarts; ``_board`` is populated on every `_update_best_lap`
call, so state-recovery replay also re-builds the in-memory board.

Threading model:
  * ``app.run()`` runs on the MAIN thread (owns SIGINT/SIGTERM handlers).
  * uvicorn runs on a DAEMON thread (no signal-handler install off-main-thread).
  * A second daemon thread seeds ``_board`` from the lakehouse 10 s after boot if
    state replay hasn't already populated it.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from quixstreams import Application

load_dotenv()

_logger = logging.getLogger(__name__)

INT_MAX = 2_147_483_647
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ---------------------------------------------------------------------------
# Settings (read once at import time, all have safe defaults)
# ---------------------------------------------------------------------------


def _first_env(*names: str) -> str | None:
    for name in names:
        val = os.environ.get(name)
        if val:
            return val
    return None


def _validate_identifier(env_name: str, value: str) -> str:
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"{env_name}={value!r} is not a valid SQL identifier "
            "(must match [A-Za-z_][A-Za-z0-9_]*)"
        )
    return value


LAKE_TABLE = _validate_identifier(
    "LAKE_TABLE", os.environ.get("LAKE_TABLE", "ac_telemetry_prod")
)
LAKE_COL_BEST_TIME = _validate_identifier(
    "LAKE_COL_BEST_TIME", os.environ.get("LAKE_COL_BEST_TIME", "iBestTime")
)
CONFIG_API_URL: str = (
    os.environ.get("CONFIG_API_URL", "http://dynamic-configuration-manager").rstrip("/")
)
SDK_TOKEN: str | None = os.environ.get("Quix__Sdk__Token")
HTTP_HOST: str = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT: int = int(os.environ.get("HTTP_PORT", "80"))
CONSUMER_GROUP: str = os.environ.get("CONSUMER_GROUP", "best-times-cache-lite")

# ---------------------------------------------------------------------------
# Module-level shared state (HTTP thread reads, SDF thread writes)
# ---------------------------------------------------------------------------

# experiment -> track -> carModel -> driver -> best_ms
_board: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
# experiment -> environment (last seen environment label for that experiment)
_board_envs: dict[str, str] = {}
_board_lock = threading.Lock()

# Most recently updated experiment (default for /best-laps with no ?experiment=)
_active_experiment: str = ""

# hostname -> {track, carModel, playerName, updated_epoch}
_session_cache: dict[str, dict[str, Any]] = {}
# hostname -> {experiment, driver, environment, track, carModel, updated_epoch}
_config_cache: dict[str, dict[str, Any]] = {}
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Board helpers
# ---------------------------------------------------------------------------


def _board_set(val: dict[str, Any]) -> None:
    """Mirror *val* into ``_board``.  Caller must NOT hold ``_board_lock``."""
    global _active_experiment
    experiment = val["experiment"]
    track = val["track"]
    car = val["carModel"]
    driver = val["driver"]
    best_ms = val["iBestTime"]
    env = val.get("environment", "")
    with _board_lock:
        _board.setdefault(experiment, {}).setdefault(track, {}).setdefault(car, {})[
            driver
        ] = best_ms
        _board_envs[experiment] = env
        _active_experiment = experiment


# ---------------------------------------------------------------------------
# Enrichment cache helpers (called from SDF update branches)
# ---------------------------------------------------------------------------


def _update_session_cache(val: dict[str, Any], key: str) -> None:
    """Update session cache from an ac-telemetry-session message."""
    track = str(val.get("track") or "").strip()
    car = str(val.get("carModel") or "").strip()
    player = str(val.get("playerName") or "").strip()
    if not (track and car):
        return
    with _cache_lock:
        _session_cache[key] = {
            "track": track,
            "carModel": car,
            "playerName": player,
            "updated_epoch": time.time(),
        }
    _logger.info(
        "session cache updated: key=%s track=%s car=%s player=%s", key, track, car, player
    )
    # Force a DCM refresh for this hostname so driver/experiment stay fresh.
    _refresh_experiment_from_dcm(key)


def _update_config_cache(val: dict[str, Any], key: str) -> None:  # noqa: ARG001
    """React to a DCM ac-telemetry-config event and update the experiment cache."""
    try:
        metadata = val.get("metadata") or {}
        if not isinstance(metadata, dict):
            return
        category = metadata.get("category")
        event_type = metadata.get("type")
        target_key = str(metadata.get("target_key") or "").strip()
        event = str(val.get("event") or "").strip().lower()

        if (
            category != "ac-telemetry"
            or event_type not in ("session", "experiment")
            or not target_key
        ):
            return

        if event == "deleted":
            with _cache_lock:
                _config_cache.pop(target_key, None)
                if event_type == "session":
                    _session_cache.pop(target_key, None)
            _logger.info("config/session cache dropped: key=%s type=%s", target_key, event_type)
            return

        # On changed: prefer the contentUrl carried by the event (avoids a
        # separate DCM list-configs round-trip).
        content_url = str(val.get("contentUrl") or "").strip()
        if content_url:
            _fetch_content_url(target_key, content_url)
        else:
            _refresh_experiment_from_dcm(target_key)
    except Exception:
        _logger.exception("failed to apply DCM config event")


def _fetch_content_url(hostname: str, content_url: str) -> None:
    """Fetch a DCM contentUrl and update ``_config_cache``."""
    headers: dict[str, str] = {}
    if SDK_TOKEN:
        headers["Authorization"] = f"Bearer {SDK_TOKEN}"
    try:
        with httpx.Client(timeout=5.0, verify=False) as client:
            resp = client.get(content_url, headers=headers)
            if resp.status_code != 200:
                _logger.warning(
                    "contentUrl fetch returned %d for key=%s", resp.status_code, hostname
                )
                return
            content = resp.json()
            if not isinstance(content, dict):
                return
        with _cache_lock:
            _config_cache[hostname] = {
                "experiment": str(content.get("experiment_id") or ""),
                "driver": str(content.get("driver") or ""),
                "environment": str(content.get("environment") or ""),
                "track": str(content.get("track") or ""),
                "carModel": str(content.get("carModel") or ""),
                "updated_epoch": time.time(),
            }
        _logger.info("config cache updated from contentUrl: key=%s", hostname)
    except Exception:
        _logger.exception("contentUrl fetch failed for key=%s", hostname)


def _refresh_experiment_from_dcm(hostname: str) -> None:
    """Fetch experiment/driver/environment from DCM API for *hostname*.

    Mirrors the proven logic from leaderboard-service and best-laps-cache
    enrichment modules: list configs → find experiment type with matching
    target_key → list versions → fetch latest version content.
    """
    if not CONFIG_API_URL:
        return
    base = f"{CONFIG_API_URL}/api/v1"
    headers: dict[str, str] = {}
    if SDK_TOKEN:
        headers["Authorization"] = f"Bearer {SDK_TOKEN}"
    try:
        with httpx.Client(timeout=5.0, verify=False) as client:
            resp = client.get(f"{base}/configurations", headers=headers)
            if resp.status_code != 200:
                _logger.warning(
                    "DCM list returned %d resolving experiment for %s",
                    resp.status_code,
                    hostname,
                )
                return
            data = resp.json()
            configs = (
                data
                if isinstance(data, list)
                else data.get("data", data.get("items", []))
            )
            config_id: str | None = None
            for cfg in configs:
                meta = cfg.get("metadata") or {}
                if (
                    meta.get("type") == "experiment"
                    and meta.get("target_key") == hostname
                ):
                    config_id = cfg.get("id") or cfg.get("_id")
                    break
            if not config_id:
                _logger.info("no experiment config in DCM for hostname=%s", hostname)
                return
            # List versions of the config
            v_resp = client.get(
                f"{base}/configurations/{config_id}/versions", headers=headers
            )
            if v_resp.status_code != 200:
                return
            versions = v_resp.json()
            if isinstance(versions, dict):
                versions = versions.get("data", versions.get("items", []))
            if not versions:
                return
            latest = max(
                versions,
                key=lambda v: int(v.get("metadata", v).get("version", 0) or 0),
            )
            version = latest.get("metadata", latest).get("version")
            c_resp = client.get(
                f"{base}/configurations/{config_id}/versions/{version}/content",
                headers=headers,
            )
            if c_resp.status_code != 200:
                return
            content = c_resp.json()
            if not isinstance(content, dict):
                return
        with _cache_lock:
            _config_cache[hostname] = {
                "experiment": str(content.get("experiment_id") or ""),
                "driver": str(content.get("driver") or ""),
                "environment": str(content.get("environment") or ""),
                "track": "",
                "carModel": "",
                "updated_epoch": time.time(),
            }
        _logger.info(
            "config cache updated from DCM: key=%s experiment=%s",
            hostname,
            _config_cache[hostname]["experiment"],
        )
    except Exception:
        _logger.exception("DCM experiment lookup failed for hostname=%s", hostname)


# ---------------------------------------------------------------------------
# Cache lookup helpers
# ---------------------------------------------------------------------------


def _latest_cache_entry(
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the most recently updated entry from a cache dict."""
    if not cache:
        return None
    return max(
        cache.values(),
        key=lambda e: float(e.get("updated_epoch") or 0.0),
    )


def _latest_with_driver(
    cache: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Return the most recently updated entry that carries a non-empty driver."""
    best_epoch = -1.0
    best_entry: dict[str, Any] | None = None
    for entry in cache.values():
        if not str(entry.get("driver") or "").strip():
            continue
        epoch = float(entry.get("updated_epoch") or 0.0)
        if epoch > best_epoch:
            best_epoch = epoch
            best_entry = entry
    return best_entry


# ---------------------------------------------------------------------------
# Core enrichment / validation (called from the raw-telemetry SDF)
# ---------------------------------------------------------------------------


def _enrich(val: dict[str, Any], key: str) -> dict[str, Any] | None:
    """Resolve (experiment, environment, track, carModel, driver, iBestTime).

    Returns a complete enriched dict, or ``None`` if any required field is
    missing or ``iBestTime`` falls outside ``(0, INT_MAX)``.
    """
    # Prefer fields already on the payload (lake-enriched replay path).
    track = str(val.get("track") or "").strip()
    car = str(val.get("carModel") or val.get("car") or "").strip()
    driver = str(val.get("driver") or "").strip()
    experiment = str(val.get("experiment") or "").strip()
    environment = str(val.get("environment") or "").strip()

    with _cache_lock:
        # Most-recent strategy: single-sim deployments have exactly one active
        # session/experiment at a time.  Using the most-recent entry from each
        # cache (per-key first, then global latest) mirrors the proven pattern
        # from leaderboard-service and the original best-laps-cache.
        session_entry = _session_cache.get(key) or _latest_cache_entry(_session_cache)
        config_with_driver = _latest_with_driver(_config_cache)
        config_any = _config_cache.get(key) or _latest_cache_entry(_config_cache)

    if not track and session_entry:
        track = str(session_entry.get("track") or "").strip()
    if not car and session_entry:
        car = str(session_entry.get("carModel") or "").strip()
    if not driver:
        if config_with_driver:
            driver = str(config_with_driver.get("driver") or "").strip()
        if not driver and session_entry:
            driver = str(session_entry.get("playerName") or "").strip()
    if not experiment and config_any:
        experiment = str(config_any.get("experiment") or "").strip()
    if not environment and config_any:
        environment = str(config_any.get("environment") or "").strip()

    # All four of these must be non-empty for a useful board key.
    if not all([experiment, track, car, driver]):
        return None

    # Validate iBestTime: must be in (0, INT_MAX).
    raw_best = val.get(LAKE_COL_BEST_TIME)
    if raw_best is None:
        return None
    try:
        best_ms = int(float(raw_best))
    except (TypeError, ValueError):
        return None
    if not (0 < best_ms < INT_MAX):
        return None

    return {
        "experiment": experiment,
        "environment": environment,
        "track": track,
        "carModel": car,
        "driver": driver,
        "iBestTime": best_ms,
    }


# ---------------------------------------------------------------------------
# Stateful best-lap update (QuixStreams stateful=True)
# ---------------------------------------------------------------------------


def _update_best_lap(val: dict[str, Any], state: Any) -> dict[str, Any]:
    """Persist best lap to RocksDB and mirror to ``_board``.

    The composite ``board_key`` encodes all four group dimensions so multiple
    experiments / tracks / cars / drivers co-exist in a single per-hostname
    RocksDB partition.

    Called on every message — including during state-recovery replay — so
    ``_board`` is rebuilt automatically when RocksDB is restored after a restart.
    """
    board_key = f"{val['experiment']}|{val['track']}|{val['carModel']}|{val['driver']}"
    current_best: int | None = state.get(board_key)
    new_best: int = val["iBestTime"]
    if current_best is None or new_best < current_best:
        state.set(board_key, new_best)
        _board_set(val)
    return val


# ---------------------------------------------------------------------------
# Lake seed (daemon thread, 10 s delay)
# ---------------------------------------------------------------------------


def _get_col(cols: list[str], col_idx: dict[str, int], col_name: str) -> str:
    i = col_idx.get(col_name, -1)
    return cols[i].strip() if 0 <= i < len(cols) else ""


def _run_lake_seed() -> None:
    """Seed ``_board`` from the lakehouse if state replay didn't populate it."""
    time.sleep(10)
    with _board_lock:
        already_populated = bool(_board)
    if already_populated:
        _logger.info("lake seed skipped: _board already populated from state recovery")
        return

    lake_url = _first_env(
        "Quix__Lakehouse__Query__Url", "LAKE_API_URL", "QUIXLAKE_URL"
    )
    if not lake_url:
        _logger.warning(
            "lake seed skipped: no lakehouse URL configured "
            "(Quix__Lakehouse__Query__Url / LAKE_API_URL)"
        )
        return

    lake_token = _first_env(
        "Quix__Lakehouse__Query__AuthToken",
        "LAKE_API_TOKEN",
        "QUIX_LAKE_TOKEN",
        "quix_lake_pat",
    )
    # byox-safe: no GROUP BY / MIN / CTE
    sql = (
        f"SELECT environment, experiment, track, carModel, driver, {LAKE_COL_BEST_TIME} "
        f"FROM {LAKE_TABLE} "
        f"WHERE {LAKE_COL_BEST_TIME} > 0 AND {LAKE_COL_BEST_TIME} < {INT_MAX}"
    )
    _logger.info("lake seed SQL: %s", sql)

    headers: dict[str, str] = {"Content-Type": "text/plain", "Accept": "text/csv"}
    if lake_token:
        headers["Authorization"] = f"Bearer {lake_token}"

    try:
        with httpx.Client(timeout=60.0, verify=False) as client:
            resp = client.post(f"{lake_url}/query", content=sql, headers=headers)
        if resp.status_code != 200:
            _logger.warning("lake seed query returned HTTP %d", resp.status_code)
            return
        text = resp.text.strip()
        if not text:
            _logger.info("lake seed: empty response")
            return
        lines = text.splitlines()
        if len(lines) < 2:
            _logger.info("lake seed: no data rows")
            return

        header = [h.strip() for h in lines[0].split(",")]
        col_idx = {col: i for i, col in enumerate(header)}
        best_col_idx = col_idx.get(LAKE_COL_BEST_TIME)
        if best_col_idx is None:
            _logger.warning("lake seed: column %s missing from response header", LAKE_COL_BEST_TIME)
            return

        # Python-side reduction to (experiment, track, carModel, driver) → min_ms.
        reduced: dict[tuple[str, str, str, str], tuple[str, int]] = {}
        for line in lines[1:]:
            if not line.strip():
                continue
            cols = line.split(",")
            if len(cols) <= best_col_idx:
                continue
            try:
                best_ms = int(float(cols[best_col_idx].strip()))
            except (ValueError, TypeError):
                continue
            if not (0 < best_ms < INT_MAX):
                continue
            env = _get_col(cols, col_idx, "environment")
            exp = _get_col(cols, col_idx, "experiment")
            track = _get_col(cols, col_idx, "track")
            car = _get_col(cols, col_idx, "carModel")
            driver = _get_col(cols, col_idx, "driver")
            if not all([exp, track, car, driver]):
                continue
            group_key = (exp, track, car, driver)
            prev = reduced.get(group_key)
            if prev is None or best_ms < prev[1]:
                reduced[group_key] = (env, best_ms)

        count = 0
        with _board_lock:
            for (exp, track, car, driver), (env, best_ms) in reduced.items():
                exp_board = _board.setdefault(exp, {})
                track_board = exp_board.setdefault(track, {})
                car_board = track_board.setdefault(car, {})
                # State recovery wins: only seed if no live value present.
                if driver not in car_board:
                    car_board[driver] = best_ms
                    _board_envs[exp] = env
                    count += 1
        _logger.info("lake seed: seeded %d new entries into _board", count)
    except Exception:
        _logger.exception("lake seed failed")


# ---------------------------------------------------------------------------
# FastAPI HTTP server
# ---------------------------------------------------------------------------

app_http = FastAPI(title="best-times-cache-lite")


@app_http.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app_http.get("/best-laps")
def best_laps(
    experiment: str = "",
    track: str = "",
    carModel: str = "",
    format: str = "csv",
) -> Any:
    """Return best laps as CSV (default) or JSON.

    If ``experiment`` is omitted, the most recently active experiment is used.
    Rows are sorted by ``iBestTime`` ascending (fastest first).
    """
    with _board_lock:
        target_exp = experiment or _active_experiment
        rows: list[dict[str, Any]] = []
        for exp, tracks in _board.items():
            if target_exp and exp != target_exp:
                continue
            env = _board_envs.get(exp, "")
            for tr, cars in tracks.items():
                if track and tr != track:
                    continue
                for car, drivers in cars.items():
                    if carModel and car != carModel:
                        continue
                    for drv, best_ms in drivers.items():
                        rows.append(
                            {
                                "environment": env,
                                "experiment": exp,
                                "track": tr,
                                "carModel": car,
                                "driver": drv,
                                "iBestTime": best_ms,
                            }
                        )

    rows.sort(key=lambda r: r["iBestTime"])

    if format.lower() == "json":
        return JSONResponse(content={"data": rows})

    # CSV — columns match the existing best-laps-cache contract
    csv_lines = ["environment,experiment,track,carModel,driver,iBestTime"]
    for row in rows:
        csv_lines.append(
            f"{row['environment']},{row['experiment']},{row['track']},"
            f"{row['carModel']},{row['driver']},{row['iBestTime']}"
        )
    return PlainTextResponse(content="\n".join(csv_lines), media_type="text/csv")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )

    app = Application(
        consumer_group=CONSUMER_GROUP,
        auto_offset_reset="latest",
    )

    raw_topic = app.topic(
        os.environ.get("output", "ac-telemetry-raw"),
        value_deserializer="json",
        key_deserializer="str",
    )
    session_topic = app.topic(
        os.environ.get("session_output", "ac-telemetry-session"),
        value_deserializer="json",
        key_deserializer="str",
    )
    config_topic = app.topic(
        os.environ.get("config_input", "ac-telemetry-config"),
        value_deserializer="json",
        key_deserializer="str",
    )

    # SDF 1: session events → update session cache + DCM refresh
    sdf_session = app.dataframe(session_topic)
    sdf_session.update(
        lambda val, key, timestamp, headers: _update_session_cache(val, key),
        metadata=True,
    )

    # SDF 2: DCM config events → update experiment cache
    sdf_config = app.dataframe(config_topic)
    sdf_config.update(
        lambda val, key, timestamp, headers: _update_config_cache(val, key),
        metadata=True,
    )

    # SDF 3: raw telemetry → enrich → filter nones → stateful best-lap update
    sdf = app.dataframe(raw_topic)
    sdf = sdf.apply(
        lambda val, key, timestamp, headers: _enrich(val, key),
        metadata=True,
    )
    sdf = sdf.filter(lambda val: val is not None)
    sdf = sdf.apply(_update_best_lap, stateful=True)

    # Start HTTP server on a daemon thread (no signal handlers off-main-thread).
    http_thread = threading.Thread(
        target=lambda: uvicorn.run(
            app_http,
            host=HTTP_HOST,
            port=HTTP_PORT,
            log_level=os.getenv("LOG_LEVEL", "info").lower(),
        ),
        name="http-server",
        daemon=True,
    )
    http_thread.start()

    # Start lake seed thread (10 s delay; skipped if state replay populated _board).
    seed_thread = threading.Thread(
        target=_run_lake_seed,
        name="lake-seed",
        daemon=True,
    )
    seed_thread.start()

    # Blocking — owns the main thread so QuixStreams can install signal handlers.
    app.run()


if __name__ == "__main__":
    main()
