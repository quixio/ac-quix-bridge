"""best-laps-lite v2 — RAM-mirror cache (single file, no classes).

Idiomatic QuixStreams (QS 3.24): one Application, one app.run(), three SDF
branches, RocksDB State as the durable store, an in-process RAM mirror
(BOARD_RAM) read by an inline FastAPI GET, and two output topics emitted on a
new/improved best. Cold-starts State from the LakeHouse when empty. track and
carModel come from the session topic (NOT from DCM); DCM (via join_lookup +
QuixConfigurationService) supplies experiment / driver / environment only.

Topology (one line each):
  session: app.dataframe(session_topic).update(remember_session, metadata=True)
           -> SESSION_BY_HOST[host] = {track, carModel, session_id}
  raw:     app.dataframe(raw_topic).join_lookup(lookup, fields)
           .apply(shape, metadata=True) .filter(is_valid) .group_by("experiment")
           .apply(handle, stateful=True, metadata=True)  [-> two to_topic branches]
  handle:  read board from State -> ALWAYS mirror BOARD_RAM[exp]+EXP_ENV[exp]
           -> lazy lake seed once (seeded flag) -> _fold tick
           -> on change: state.set("board") + annotate snapshot/event for emit

Threading mirrors best-laps-cache: app.run() owns the MAIN thread (it installs
SIGINT/SIGTERM via signal.signal); uvicorn runs on a worker daemon thread
(off-main -> uvicorn capture_signals is a no-op, so no signal clash). On
SIGTERM, app.run() returns and the process exits, tearing down the daemon HTTP
thread. There is no boot-seed thread — seeding is lazy inside handle.
"""

from __future__ import annotations

import copy
import csv
import io
import logging
import os
import sys
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from quixstreams import Application
from quixstreams.dataframe.joins.lookups import QuixConfigurationService

INT_MAX = 2147483647  # AC "no lap set" sentinel — never store/serve it

# Column order the dashboard's leaderboard path consumes — a drop-in replica of
# best-laps-cache/best_laps_cache/api.py's `_CSV_COLUMNS` so the dashboard's
# `/leaderboard` -> GET /best-laps path works against lite v2 unchanged.
_CSV_COLUMNS = ["environment", "experiment", "track", "carModel", "driver", "iBestTime"]

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("best-laps-lite")

# --- config from env ---
LAKE_URL = os.environ.get("Quix__Lakehouse__Query__Url") or os.environ.get("LAKE_API_URL")
LAKE_TOKEN = os.environ.get("Quix__Lakehouse__Query__AuthToken") or os.environ.get(
    "LAKE_API_TOKEN"
)
LAKE_TABLE = os.environ.get("LAKE_TABLE", "ac_telemetry_prod")
BEST_COL = os.environ.get("LAKE_COL_BEST_TIME", "iBestTime")
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "80"))

# --- in-process projections (read by the HTTP thread; never State off-thread) ---
# Latest session per host (key == hostname), the source of track/carModel/session_id.
SESSION_BY_HOST: dict[str, dict] = {}
# RAM mirror of the State board, keyed by experiment: {exp: {track:{car:{driver: ms}}}}.
BOARD_RAM: dict[str, dict] = {}
# Environment per experiment (for rows-mode output); mirrored alongside the board.
EXP_ENV: dict[str, str] = {}
# Guards BOARD_RAM/EXP_ENV against the cross-thread race: the SDF main thread
# mutates the nested board in _fold while the uvicorn daemon thread serializes it
# for a GET (otherwise an intermittent "dictionary changed size during iteration"
# 500). handle publishes a deep copy under the lock; readers snapshot under it.
_RAM_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested offline)
# --------------------------------------------------------------------------- #
def query_lake(experiment: str) -> list[dict]:
    """Scan the whole table for one experiment -> rows (best per driver via fold).

    Carried verbatim from v1: POST raw SQL to {LAKE_URL}/query, verify=False
    (byox self-signed cert), Bearer token if present, parse CSV, raise on error.
    """
    exp = experiment.replace("'", "''")
    sql = (
        f"SELECT track, carModel, driver, {BEST_COL} FROM {LAKE_TABLE} "
        f"WHERE {BEST_COL} > 0 AND {BEST_COL} < {INT_MAX} AND experiment = '{exp}'"
    )
    headers = {"Content-Type": "text/plain"}
    if LAKE_TOKEN:
        headers["Authorization"] = f"Bearer {LAKE_TOKEN}"
    resp = httpx.post(
        f"{LAKE_URL.rstrip('/')}/query",
        content=sql,
        headers=headers,
        timeout=30.0,
        verify=False,
    )
    if resp.text.lstrip().startswith("# ERROR:"):
        raise RuntimeError(resp.text)
    return list(csv.DictReader(io.StringIO(resp.text)))


def _fold(board: dict, row: dict) -> tuple[bool, int | None]:
    """Min-update board[track][car][driver]; INT_MAX/<=0/blank -> no-op.

    Returns ``(changed, previous_ms)``:
      * first insert for that driver -> ``(True, None)``
      * strict improvement          -> ``(True, old_ms)``
      * slower / equal / invalid     -> ``(False, old_ms)`` (or ``(False, None)``)
    The previous value lets the event carry previous_best_ms / delta_ms /
    first_for_driver.
    """
    try:
        best = int(row[BEST_COL])
    except (TypeError, ValueError, KeyError):
        return False, None
    track, car, drv = row.get("track"), row.get("carModel"), row.get("driver")
    if not (track and car and drv) or not (0 < best < INT_MAX):
        cur = board.get(track, {}).get(car, {}).get(drv) if (track and car and drv) else None
        return False, cur
    cur = board.get(track, {}).get(car, {}).get(drv)
    if cur is None or best < cur:
        board.setdefault(track, {}).setdefault(car, {})[drv] = best
        return True, cur
    return False, cur


def shape(value: dict, key, timestamp, headers) -> dict:
    """Project a join_lookup'd raw tick: DCM fields + session-sourced track/car.

    ``key`` is the message key == hostname; track/carModel come from the latest
    session seen for that host (SESSION_BY_HOST), NOT from DCM.
    """
    sess = SESSION_BY_HOST.get(key, {})
    return {
        "experiment": value.get("experiment", ""),
        "driver": value.get("driver", ""),
        "environment": value.get("environment", ""),
        "track": sess.get("track", ""),
        "carModel": sess.get("carModel", ""),
        "session_id": sess.get("session_id", ""),
        BEST_COL: int(value.get(BEST_COL) or 0),
    }


def is_valid(value: dict) -> bool:
    """All of experiment/track/carModel/driver non-empty AND 0 < iBestTime < INT_MAX.

    A tick with no session yet (blank track/car) fails here and is dropped.
    """
    return (
        bool(value["experiment"] and value["track"] and value["carModel"] and value["driver"])
        and 0 < value[BEST_COL] < INT_MAX
    )


def to_rows(boards: dict[str, dict], envs: dict[str, str]) -> list[dict]:
    """Flatten the nested boards into best-laps-cache's flat row contract.

    One row per (experiment, track, carModel, driver), fastest-first within each
    track/carModel group, carrying the per-experiment environment.
    """
    rows: list[dict] = []
    for exp, board in boards.items():
        env = envs.get(exp, "")
        for track, cars in board.items():
            for car, drivers in cars.items():
                for drv, ms in drivers.items():
                    rows.append(
                        {
                            "environment": env,
                            "experiment": exp,
                            "track": track,
                            "carModel": car,
                            "driver": drv,
                            "iBestTime": int(ms),
                        }
                    )
    rows.sort(key=lambda r: (r["experiment"], r["track"], r["carModel"], r["iBestTime"]))
    return rows


def _to_csv(rows: list[dict]) -> str:
    """Serialize rows to CSV in the exact _CSV_COLUMNS order (header + rows).

    Drop-in replica of best-laps-cache's _to_csv so the dashboard parses lite v2
    identically. Empty rows -> header line only.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def to_best_time_payload(value: dict) -> dict:
    """Full-board snapshot for the best_time topic (key == experiment)."""
    return {
        "experiment": value["experiment"],
        "board": value["_board"],
        "timestamp_ms": value["_timestamp_ms"],
    }


def to_event_payload(value: dict) -> dict:
    """Rich per-new-best event for the event topic (key == experiment)."""
    prev = value["_previous_ms"]
    best = value[BEST_COL]
    return {
        "type": "new_best",
        "experiment": value["experiment"],
        "environment": value.get("environment", ""),
        "track": value["track"],
        "carModel": value["carModel"],
        "driver": value["driver"],
        "best_ms": best,
        "previous_best_ms": prev,
        "delta_ms": (best - prev) if prev is not None else None,
        "first_for_driver": prev is None,
        "session_id": value.get("session_id", ""),
        "timestamp_ms": value["_timestamp_ms"],
    }


# --------------------------------------------------------------------------- #
# Stateful + side-effecting ops
# --------------------------------------------------------------------------- #
def remember_session(value: dict, key, timestamp, headers) -> None:
    """Session branch: latest-wins {track, carModel, session_id} per host."""
    SESSION_BY_HOST[key] = {
        "track": value.get("track", ""),
        "carModel": value.get("carModel", ""),
        "session_id": value.get("session_id", ""),
    }


def handle(value: dict, state, key, timestamp, headers) -> dict:
    """Per-experiment stateful core (keyed by experiment after group_by).

    Reads board from State; lazily seeds State from the lake once per experiment;
    folds the tick; on a new/improved best, writes State and annotates the value
    for the to_topic branches. State writes + topic emits happen ONLY on a change.

    At the end it publishes the board into the RAM mirror under ``_RAM_LOCK`` as a
    DEEP COPY (so the stored mirror is never mutated in place by a later tick),
    whenever the content changed OR RAM is still cold for this experiment. The
    "cold" clause re-hydrates RAM from durable State on the first raw tick of any
    kind after a restart (always-warm RAM, §1a Q1/Q3) without paying a per-tick
    deepcopy on every non-best tick.
    """
    exp = value["experiment"]
    board = state.get("board") or {}

    # Lazy one-time lake seed per experiment partition (gated by `seeded`).
    if not state.get("seeded"):
        try:
            for r in query_lake(exp):
                _fold(board, r)
        except Exception:  # a lake hiccup must not crash the fold; live ticks still build State
            logger.exception("lake seed failed for experiment=%s — continuing live-only", exp)
        state.set("seeded", True)
        state.set("board", board)

    changed, previous_ms = _fold(board, value)
    value["_changed"] = changed
    value["_board"] = board
    value["_previous_ms"] = previous_ms
    value["_timestamp_ms"] = int(timestamp) if timestamp is not None else int(time.time() * 1000)

    if changed:
        state.set("board", board)
        boards_n = sum(
            len(d) for cars in board.values() for d in cars.values()
        )
        logger.info(
            "new best exp=%s track=%s car=%s driver=%s ms=%s (board boards=%d)",
            exp,
            value["track"],
            value["carModel"],
            value["driver"],
            value[BEST_COL],
            boards_n,
        )

    # Publish to RAM under the lock as a deep copy (decoupled from `board`).
    env = value.get("environment", "") or EXP_ENV.get(exp, "")
    if changed or exp not in BOARD_RAM:
        with _RAM_LOCK:
            BOARD_RAM[exp] = copy.deepcopy(board)
            EXP_ENV[exp] = env
    return value


# --------------------------------------------------------------------------- #
# QuixStreams app + topics + SDF branches
# --------------------------------------------------------------------------- #
app = Application(
    broker_address=os.environ.get("BROKER_ADDRESS") or None,
    consumer_group=os.environ.get("CONSUMER_GROUP", "best-laps-lite"),
    auto_offset_reset="earliest",
    state_dir=os.environ.get("Quix__State__Dir", "state"),
)

raw_topic = app.topic(os.environ.get("output", "ac-telemetry-raw"), value_deserializer="json")
session_topic = app.topic(
    os.environ.get("session_output", "ac-telemetry-session"), value_deserializer="json"
)
config_topic = app.topic(os.environ.get("config_input", "ac-telemetry-config"))
best_time_topic = app.topic(
    os.environ.get("best_time_output", "ac-best-laps"), value_serializer="json"
)
event_topic = app.topic(
    os.environ.get("event_output", "ac-best-laps-events"), value_serializer="json"
)

# DCM enrichment: experiment / driver / environment ONLY (track/car now from session).
lookup = QuixConfigurationService(config_topic, app_config=app.config, fallback="default")
fields = {
    "experiment": lookup.json_field("$.experiment_id", type="experiment", default=""),
    "driver": lookup.json_field("$.driver", type="experiment", default=""),
    "environment": lookup.json_field("$.environment", type="experiment", default=""),
}

# Branch 1 — session: keep latest {track, carModel, session_id} per host.
app.dataframe(session_topic).update(remember_session, metadata=True)

# Branch 2 — raw: enrich -> shape -> validate -> re-key -> fold (in handle).
sdf = app.dataframe(raw_topic).join_lookup(lookup, fields)
sdf = sdf.apply(shape, metadata=True)
sdf = sdf.filter(is_valid)
sdf = sdf.group_by("experiment")
sdf = sdf.apply(handle, stateful=True, metadata=True)

# Branch 3 — outputs: emit ONLY on a new/improved best (key == experiment).
changed = sdf.filter(lambda v: v["_changed"])
changed.apply(to_best_time_payload).to_topic(best_time_topic, key=lambda v: v["experiment"])
changed.apply(to_event_payload).to_topic(event_topic, key=lambda v: v["experiment"])


# --------------------------------------------------------------------------- #
# Inline FastAPI — serves GET from RAM only (never State off-thread)
# --------------------------------------------------------------------------- #
def create_http_app() -> FastAPI:
    api = FastAPI(title="best-laps-lite", version="2.0.0")

    @api.get("/healthz")
    def healthz() -> dict:
        # Snapshot under the lock — never inspect the live dict the SDF mutates.
        with _RAM_LOCK:
            boards = list(BOARD_RAM)
        return {
            "status": "ok",
            "experiments": boards,
            "boards": len(boards),
        }

    @api.get("/best-laps")
    def best_laps(
        environment: str | None = Query(None),  # accepted, not a filter (single env)
        experiment: str | None = Query(None),
        track: str | None = Query(None),
        carModel: str | None = Query(None),  # noqa: N803 — public query-param name
        driver: str | None = Query(None),  # accepted for back-compat; filter only if given
        format: str = Query("csv"),  # noqa: A002 — public query-param name
    ):
        # Snapshot RAM under the lock, then build the response from the snapshot —
        # never serialize the live dicts the SDF thread mutates in _fold/handle.
        with _RAM_LOCK:
            all_boards = copy.deepcopy(BOARD_RAM)
            envs = dict(EXP_ENV)

        # Target experiment: the explicit param selects that board; omitted means
        # ALL experiments (lite has no single "active experiment" like the cache).
        if experiment is not None:
            boards = {experiment: all_boards.get(experiment, {})}
        else:
            boards = all_boards

        # Nested mode (kept available; NOT the default).
        if format.lower() == "nested":
            as_of = time.time()
            if experiment is not None:
                return JSONResponse(
                    {
                        "experiment": experiment,
                        "board": boards.get(experiment, {}),
                        "as_of_epoch": as_of,
                        "source": "best-laps-lite-ram",
                    }
                )
            return JSONResponse(
                {
                    "boards": boards,
                    "experiments": list(boards),
                    "as_of_epoch": as_of,
                    "source": "best-laps-lite-ram",
                }
            )

        # Flat dashboard contract (default csv / json). Flatten -> filter -> sort.
        rows = to_rows(boards, envs)
        if track is not None:
            rows = [r for r in rows if r["track"] == track]
        if carModel is not None:
            rows = [r for r in rows if r["carModel"] == carModel]
        if driver:  # accepted for back-compat; the dashboard overlays "me" client-side
            rows = [r for r in rows if r["driver"] == driver]
        rows.sort(key=lambda r: (r["track"], r["carModel"], r["iBestTime"]))

        applied = {
            k: v
            for k, v in {
                "experiment": experiment,
                "track": track,
                "carModel": carModel,
                "driver": driver,
            }.items()
            if v is not None
        } or "none"
        logger.info(
            "GET /best-laps filters=%s -> %d rows (format=%s)",
            applied,
            len(rows),
            format.lower(),
        )
        if format.lower() == "json":
            return JSONResponse(
                {
                    "table": LAKE_TABLE,
                    "columns": _CSV_COLUMNS,
                    "rows": rows,
                    "row_count": len(rows),
                    "source": "best-laps-lite",
                    "as_of_epoch": time.time(),
                }
            )
        return PlainTextResponse(_to_csv(rows), media_type="text/csv")

    return api


def _serve_http() -> None:
    """Run uvicorn on a worker thread (no signal handlers off-main-thread)."""
    server = uvicorn.Server(
        uvicorn.Config(
            create_http_app(),
            host=HTTP_HOST,
            port=HTTP_PORT,
            log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        )
    )
    server.run()


if __name__ == "__main__":
    logger.info(
        "boot: raw=%s session=%s config=%s best_time=%s event=%s "
        "lake_table=%s best_col=%s http=%s:%d cg=%s state_dir=%s lake_url=%s",
        raw_topic.name,
        session_topic.name,
        config_topic.name,
        best_time_topic.name,
        event_topic.name,
        LAKE_TABLE,
        BEST_COL,
        HTTP_HOST,
        HTTP_PORT,
        os.environ.get("CONSUMER_GROUP", "best-laps-lite"),
        os.environ.get("Quix__State__Dir", "state"),
        "yes" if LAKE_URL else "no",
    )
    http_thread = threading.Thread(target=_serve_http, name="http-server", daemon=True)
    http_thread.start()
    logger.info("app.run() on MAIN thread; uvicorn on daemon thread '%s'", http_thread.name)
    app.run()  # blocking, MAIN thread; installs SIGINT/SIGTERM
