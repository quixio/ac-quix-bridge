"""best-laps-lite — State-as-truth cache with cold-start boot seed (single file).

Idiomatic QuixStreams (QS 3.24): one Application, one app.run(). **RocksDB State
is the ground truth; the in-process BOARD_RAM mirror is a projection** re-built
from State on EVERY consumed message (RAM never leads State). An inline FastAPI
GET serves from BOARD_RAM (csv/json/nested). track/carModel come from the
session topic (NOT DCM); DCM (join_lookup + ProdDCMConfigurationService) supplies
experiment/driver/environment only.

Topology — events-topic indirection (so the boot seeder can write State for ALL
experiments in-context):
  session: app.dataframe(session_topic).update(remember_session, metadata=True)
           -> SESSION_BY_HOST[host] = {track, carModel, session_id}
  raw:     app.dataframe(raw_topic).join_lookup(lookup, fields)
           .apply(shape, metadata=True) .filter(is_valid)
           .group_by("experiment") .apply(tag_lap) .to_topic(EVENTS_TOPIC)   # {type:"lap", ...}
  events:  app.dataframe(EVENTS_TOPIC).apply(handle, stateful=True, metadata=True)
           -> filter(_changed) -> two to_topic (ac-best-laps snapshot, ac-best-laps-events event)
  handle:  read board from State -> ALWAYS re-project BOARD_RAM[exp]+EXP_ENV[exp]
           -> dispatch type: "seed" folds carried rows idempotently (min-update,
              State+RAM, no emit); "lap" folds the tick, and on a strict
              improvement sets State + annotates snapshot/event for emit.

Cold-start boot seed (worker daemon thread, started before app.run()):
  Authoritative gate = a State-native `seeded` flag (GATE_KEY), read via a
  round-trip that ALSO serves as the latest-offset readiness barrier (one
  mechanism). The seeder re-produces a {type:"seed_gate"} event keyed GATE_KEY
  every ~2s (bounded ~120s) until handle reads state["seeded"] in-context, records
  it, and sets _GATE_EVENT — which also proves the events consumer is live and
  positioned (a message produced before assignment at latest-offset is skipped, so
  re-producing defeats that race). If the flag is set -> WARM (retained volume +
  same consumer group) -> skip the lake. Else -> COLD -> ONE aggregated MIN/GROUP
  BY lake query (all experiments, OOM-safe; retry 2-3x @ ~60s, then fail-soft),
  reduce, group by experiment, produce one {type:"seed", ...} per experiment to
  EVENTS_TOPIC (now guaranteed to land after the consumer's position), then a
  {type:"mark_seeded"} so handle sets state["seeded"]=True. The stateful handle
  folds each seed into state["board"] in-context (the durable write). The flag is
  scoped to <Quix__State__Dir>/<consumer_group>, so a volume wipe OR a group change
  drops it -> reseed. auto_offset_reset="latest": history from the seed, live laps
  from the tail.

Accepted residual: a seeded-but-never-driven experiment is in State (durable)
but absent from BOARD_RAM after a WARM restart until a message for it arrives —
RAM re-warms on traffic. After a COLD seed the seed message itself re-projects
the board into RAM (no live tick needed). Eliminating it would require replaying
State into RAM at boot, which State's in-context-only access forbids.

Threading mirrors best-laps-cache: app.run() owns the MAIN thread (it installs
SIGINT/SIGTERM via signal.signal); uvicorn + the boot seeder run on worker daemon
threads (off-main -> uvicorn capture_signals is a no-op, so no signal clash). On
SIGTERM, app.run() returns and the process exits, tearing down the daemon threads.
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
from urllib.parse import urlsplit, urlunsplit

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
# Prod-edge DCM base. The byox in-cluster DCM stamps contentUrl=
# http://dynamic-configuration-manager (an EMPTY DCM), so the native lookup
# fetches empty content and enrichment returns blank experiment/driver. We
# rewrite scheme+host of each contentUrl to this base (the real prod configs).
# Falsy -> no rewrite (other envs where the in-cluster contentUrl is correct).
DCM_CONTENT_BASE = os.environ.get("CONFIG_API_URL")
HTTP_HOST = os.environ.get("HTTP_HOST", "0.0.0.0")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "80"))
CONSUMER_GROUP = os.environ.get("CONSUMER_GROUP", "best-laps-lite")
STATE_DIR = os.environ.get("Quix__State__Dir", "state")
# Internal experiment-keyed events topic: raw laps AND boot-seed messages funnel
# here so ONE stateful SDF (one stream_id -> one RocksDB store) folds all
# experiments into State. Env-overridable.
EVENTS_TOPIC = os.environ.get("events_topic", "best-laps-events")

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

# Boot-seed gate (State-native flag) + readiness barrier, folded into ONE
# round-trip. With auto_offset_reset="latest" the events consumer positions at the
# tail on assignment, so a message produced BEFORE assignment is skipped. The boot
# seeder re-produces a {type:"seed_gate"} event keyed GATE_KEY until `handle`
# processes one (proving the consumer is live AND reading the gate's State flag
# in-context) and sets _GATE_EVENT with the flag in _GATE_RESULT. The flag lives
# in RocksDB State (scoped to <state_dir>/<consumer_group>), so it is naturally
# absent after a state-volume wipe OR a consumer-group change -> reseed. Mirrors
# best-laps-cache's GATE_KEY/mark_seeded pattern.
GATE_KEY = "__seed_gate__"
_GATE_EVENT = threading.Event()
_GATE_RESULT: dict[str, bool] = {}

# Diagnostic log throttles for the ~50Hz hot path (shape/is_valid). Each logs its
# first N events at INFO then suppresses, so the enrichment/drop reason is visible
# at startup without flooding the log. Rare paths (DCM fetch, session) are unthrottled.
_SHAPE_LOG_BUDGET = 30
_DROP_LOG_BUDGET = 30


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested offline)
# --------------------------------------------------------------------------- #
def rewrite_content_url(content_url: str, base: str | None) -> str:
    """Swap scheme+host of a DCM contentUrl to *base*, keeping path/query/fragment.

    If *base* is falsy, return *content_url* unchanged (other envs where the
    in-cluster contentUrl is already correct). Otherwise take *base*'s scheme and
    netloc and *content_url*'s path/query/fragment. A trailing slash or any path
    on *base* is ignored — only its scheme+netloc are used.
    """
    if not base:
        return content_url
    b = urlsplit(base)
    parts = urlsplit(content_url)
    return urlunsplit((b.scheme, b.netloc, parts.path, parts.query, parts.fragment))


def build_reconcile_sql(lake_table: str, best_col: str) -> str:
    """Aggregated all-experiments scan — one row per (env, exp, track, car, driver).

    Mirrors best-laps-cache/seed.build_reconcile_sql: a single-level MIN/GROUP BY
    (NO CTE — feedback_quixlake_no_cte; NO per-experiment raw scan — OOM-safe).
    The lakehouse returns at most one row per driver group instead of every 50 Hz
    tick. Identifiers come from validated env vars, so inlining is safe.
    """
    return (
        f"SELECT environment, experiment, track, carModel, driver, "
        f"MIN({best_col}) AS {best_col} "
        f"FROM {lake_table} "
        f"WHERE {best_col} > 0 AND {best_col} < {INT_MAX} "
        f"GROUP BY environment, experiment, track, carModel, driver"
    )


def query_lake_once(sql: str) -> list[dict]:
    """POST one SQL query to the lakehouse and parse the CSV rows.

    byox mechanics: POST raw SQL to {LAKE_URL}/query, verify=False (self-signed),
    Bearer if a token is set. Raises on a `# ERROR:` body or any transport error
    (the caller's retry/fail-soft loop handles it).
    """
    headers = {"Content-Type": "text/plain"}
    if LAKE_TOKEN:
        headers["Authorization"] = f"Bearer {LAKE_TOKEN}"
    resp = httpx.post(
        f"{LAKE_URL.rstrip('/')}/query",
        content=sql,
        headers=headers,
        timeout=60.0,
        verify=False,
    )
    if resp.text.lstrip().startswith("# ERROR:"):
        raise RuntimeError(resp.text)
    return list(csv.DictReader(io.StringIO(resp.text)))


def query_lake_with_retry(sql: str, retries: int = 3, backoff_s: float = 60.0) -> list[dict] | None:
    """Run the seed query, retrying transport/timeout errors with a backoff.

    QuixLake can time out on aggregated tables (feedback_quixlake_aggregation_slow).
    Retry up to *retries* times with ~*backoff_s* between attempts; after the last
    failure return ``None`` (fail-soft — the caller leaves State un-seeded and a
    later boot retries while the volume is still cold). A successful query returns
    the row list (possibly empty).
    """
    for attempt in range(1, retries + 1):
        try:
            return query_lake_once(sql)
        except Exception as exc:  # transport/timeout/HTTP error — retry then fail soft
            if attempt < retries:
                logger.warning(
                    "boot-seed lake query attempt %d/%d failed (%r); retrying in %.0fs",
                    attempt,
                    retries,
                    exc,
                    backoff_s,
                )
                time.sleep(backoff_s)
            else:
                logger.warning(
                    "boot-seed lake query failed after %d attempts (%r); leaving "
                    "State un-seeded (a later boot retries while the volume is cold)",
                    retries,
                    exc,
                )
    return None


def reduce_rows(rows: list[dict]) -> dict[tuple[str, str, str, str, str], int]:
    """Reduce lake rows to ``{(env, exp, track, car, driver): min_ms}``.

    Drops blank-driver / non-positive / INT_MAX rows. Mirrors
    best-laps-cache/seed.reduce_rows; the key is the five-tuple of partition
    fields so seed messages can be grouped by experiment without re-parsing.
    """
    out: dict[tuple[str, str, str, str, str], int] = {}
    for row in rows:
        driver = str(row.get("driver") or "").strip()
        if not driver:
            continue
        raw_best = row.get(BEST_COL)
        if raw_best is None or raw_best == "":
            continue
        try:
            best_ms = int(float(raw_best))
        except (TypeError, ValueError):
            continue
        if not (0 < best_ms < INT_MAX):
            continue
        key = (
            str(row.get("environment") or "").strip(),
            str(row.get("experiment") or "").strip(),
            str(row.get("track") or "").strip(),
            str(row.get("carModel") or "").strip(),
            driver,
        )
        prev = out.get(key)
        if prev is None or best_ms < prev:
            out[key] = best_ms
    return out


def build_seed_messages(reduced: dict[tuple[str, str, str, str, str], int]) -> list[dict]:
    """Group reduced bests by experiment into one ``{type:"seed", ...}`` per exp.

    Mirrors best-laps-cache/boot_seed.{group_reduced_by_experiment,build_seed_messages}.
    Output rides the EVENTS_TOPIC JSON contract and is folded in-context by
    ``handle``. Blank experiments are skipped; the per-experiment environment is
    the first non-blank env seen for that experiment.
    """
    grouped: dict[str, dict] = {}
    for (env, exp, track, car, driver), best_ms in reduced.items():
        if not exp:
            continue
        bucket = grouped.setdefault(exp, {"environment": "", "rows": []})
        if env and not bucket["environment"]:
            bucket["environment"] = env
        bucket["rows"].append(
            {"track": track, "carModel": car, "driver": driver, "best_lap_ms": int(best_ms)}
        )
    return [
        {
            "type": "seed",
            "experiment": exp,
            "environment": payload["environment"],
            "rows": payload["rows"],
        }
        for exp, payload in grouped.items()
    ]


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
    result = {
        "experiment": value.get("experiment", ""),
        "driver": value.get("driver", ""),
        "environment": value.get("environment", ""),
        "track": sess.get("track", ""),
        "carModel": sess.get("carModel", ""),
        "session_id": sess.get("session_id", ""),
        BEST_COL: int(value.get(BEST_COL) or 0),
    }
    # Throttled diagnostic: log the first N enriched ticks so we can see whether
    # DCM (experiment/driver/environment) and session (track/carModel) resolved.
    global _SHAPE_LOG_BUDGET
    if _SHAPE_LOG_BUDGET > 0:
        _SHAPE_LOG_BUDGET -= 1
        logger.info(
            "enrich[#%d] key=%s experiment=%r driver=%r environment=%r "
            "track=%r carModel=%r iBestTime=%s",
            30 - _SHAPE_LOG_BUDGET,
            key,
            result["experiment"],
            result["driver"],
            result["environment"],
            result["track"],
            result["carModel"],
            result[BEST_COL],
        )
        if _SHAPE_LOG_BUDGET == 0:
            logger.info("enrichment logging budget exhausted, suppressing further")
    return result


def is_valid(value: dict) -> bool:
    """All of experiment/track/carModel/driver non-empty AND 0 < iBestTime < INT_MAX.

    A tick with no session yet (blank track/car) fails here and is dropped.
    """
    ok = (
        bool(value["experiment"] and value["track"] and value["carModel"] and value["driver"])
        and 0 < value[BEST_COL] < INT_MAX
    )
    # Throttled diagnostic: on the first N drops, log which field(s) are missing.
    if not ok:
        global _DROP_LOG_BUDGET
        if _DROP_LOG_BUDGET > 0:
            _DROP_LOG_BUDGET -= 1
            logger.info(
                "DROP[#%d]: experiment=%s track=%s carModel=%s driver=%s ibest_ok=%s",
                30 - _DROP_LOG_BUDGET,
                bool(value["experiment"]),
                bool(value["track"]),
                bool(value["carModel"]),
                bool(value["driver"]),
                0 < value[BEST_COL] < INT_MAX,
            )
            if _DROP_LOG_BUDGET == 0:
                logger.info("drop logging budget exhausted, suppressing further")
    return ok


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
    sess = SESSION_BY_HOST[key]
    logger.info(
        "session cached: host=%s track=%r carModel=%r session_id=%r",
        key,
        sess["track"],
        sess["carModel"],
        sess["session_id"],
    )


def _project_ram(exp: str, board: dict, env: str) -> None:
    """Re-project a State board into the RAM mirror under the lock (deep copy).

    State is ground truth; RAM is a projection. The deep copy decouples the
    published mirror from the in-context ``board`` so a later in-place fold never
    mutates what a concurrent GET is serializing.
    """
    with _RAM_LOCK:
        BOARD_RAM[exp] = copy.deepcopy(board)
        EXP_ENV[exp] = env or EXP_ENV.get(exp, "")


def handle(value: dict, key, timestamp, headers, state) -> dict:  # QS apply(stateful+metadata) order: value,key,ts,headers,state
    """The ONE stateful op (State keyed by experiment via the events topic).

    Dispatches on ``value["type"]``:
      * ``"seed_gate"`` — the boot-seed gate + readiness probe (one mechanism).
        Reads the State-native ``seeded`` flag IN-CONTEXT, records it in
        ``_GATE_RESULT`` and sets ``_GATE_EVENT`` (the consumer processing this
        proves it is live and positioned). No fold/State-write/emit.
      * ``"mark_seeded"`` — set ``state["seeded"]=True`` (the durable gate write
        after a successful seed). No emit.
      * ``"seed"`` — fold the carried lake rows into the board IDEMPOTENTLY via
        ``_fold`` (min-update never clobbers a populated/faster value); set State
        + re-project RAM; does NOT emit (``_changed`` stays False).
      * ``"lap"`` (or untyped legacy) — fold the live tick; on a strict
        improvement set State and annotate ``_changed/_board/_previous_ms/
        _timestamp_ms`` for the output branch.

    State is the ground truth; on every fold-carrying message (seed/lap) it
    re-projects ``state["board"]`` into BOARD_RAM/EXP_ENV (RAM never leads State).
    The returned value carries ``_changed`` (the output branch filters on it).
    """
    value["_changed"] = False
    msg_type = value.get("type")

    if msg_type == "seed_gate":
        # Boot-seed gate read + readiness signal (one round-trip). Read the
        # State-native flag in-context and hand it back; the boot thread waits on
        # _GATE_EVENT. No fold, no State write, no RAM touch, never emits.
        _GATE_RESULT["seeded"] = bool(state.get("seeded"))
        _GATE_EVENT.set()
        return value

    if msg_type == "mark_seeded":
        # Boot-seed gate write: set the durable flag so a later boot (retained
        # volume + same consumer group) skips the lake query. No emit.
        state.set("seeded", True)
        logger.info("handle: mark_seeded -> state[seeded]=True")
        return value

    exp = value.get("experiment", "")
    board = state.get("board") or {}

    if msg_type == "seed":
        folded = 0
        for r in value.get("rows", []):
            # seed rows carry best_lap_ms; map to the BEST_COL key _fold reads.
            changed, _prev = _fold(
                board,
                {
                    "track": r.get("track"),
                    "carModel": r.get("carModel"),
                    "driver": r.get("driver"),
                    BEST_COL: r.get("best_lap_ms"),
                },
            )
            folded += int(changed)
        if folded:
            state.set("board", board)
        _project_ram(exp, board, value.get("environment", ""))
        logger.info(
            "handle: seed folded exp=%s rows=%d new=%d -> state[board] %s, BOARD_RAM mirrored",
            exp,
            len(value.get("rows", [])),
            folded,
            "set" if folded else "unchanged",
        )
        return value

    # type == "lap" (or untyped legacy): fold the live tick.
    changed, previous_ms = _fold(board, value)
    value["_changed"] = changed
    value["_board"] = board
    value["_previous_ms"] = previous_ms
    value["_timestamp_ms"] = int(timestamp) if timestamp is not None else int(time.time() * 1000)

    if changed:
        state.set("board", board)
        boards_n = sum(len(d) for cars in board.values() for d in cars.values())
        logger.info(
            "new best exp=%s track=%s car=%s driver=%s ms=%s (board boards=%d)",
            exp,
            value.get("track"),
            value.get("carModel"),
            value.get("driver"),
            value.get(BEST_COL),
            boards_n,
        )

    # State is truth -> re-project RAM on EVERY message (not only on change).
    _project_ram(exp, board, value.get("environment", ""))
    return value


def tag_lap(value: dict) -> dict:
    """Tag a shaped+validated raw tick as a ``"lap"`` event for EVENTS_TOPIC."""
    value["type"] = "lap"
    return value


def wait_for_seed_gate(produce_gate, total_s: float = 120.0, interval_s: float = 2.0) -> bool:
    """Drive the gate round-trip until ``_GATE_EVENT`` is set; return the readiness.

    This is BOTH the boot-seed gate read AND the latest-offset readiness barrier
    (one mechanism). With auto_offset_reset="latest" the events consumer reads from
    the tail, so a gate event produced before assignment is missed. Re-producing one
    ``seed_gate`` every *interval_s* defeats that race: the first gate event read by
    ``handle`` records ``state["seeded"]`` into ``_GATE_RESULT`` and sets
    ``_GATE_EVENT``. Returns True once the event is set (gate answered + consumer
    confirmed live), or False if *total_s* elapses first (caller proceeds as if not
    seeded — the idempotent fold is the safety net).

    *produce_gate* is a no-arg callable producing one ``seed_gate`` message;
    injected so this is unit-testable without a broker.
    """
    deadline = time.monotonic() + total_s
    while True:
        produce_gate()
        if _GATE_EVENT.wait(timeout=interval_s):
            return True
        if time.monotonic() >= deadline:
            return False


def run_boot_seed() -> bool:
    """Cold-start seed gated on a State-native ``seeded`` flag (round-trip).

    Runs on a worker daemon thread before app.run(). The authoritative gate is the
    State flag at ``GATE_KEY`` — held in RocksDB State scoped to
    <state_dir>/<consumer_group>, so it is naturally absent after a state-volume
    wipe OR a consumer-group change, either of which triggers a reseed. Mirrors
    best-laps-cache's GATE_KEY/mark_seeded.

    Flow:
      1. GATE + READINESS round-trip: ``wait_for_seed_gate`` re-produces a
         ``seed_gate`` every ~2s (bounded ~120s) until ``handle`` reads the flag
         in-context and sets ``_GATE_EVENT``. This also defeats the latest-offset
         race (a message produced before consumer assignment is skipped) and
         confirms the consumer is live before any seed is produced.
      2. If the flag is True -> State already seeded (retained volume + same group)
         -> skip the lake, return False.
      3. Else -> aggregated MIN/GROUP BY query (retry/fail-soft), reduce, group by
         experiment, produce ``{type:"seed", ...}`` per experiment, then produce
         ``{type:"mark_seeded"}`` so ``handle`` sets ``state["seeded"]=True``.

    Never raises — any failure logs a WARNING and leaves the flag unset so a later
    boot retries.
    """
    try:
        topic = events_topic
        logger.info(
            "boot: state_dir=%s cg=%s offset=latest -> gating on State seeded flag",
            STATE_DIR,
            CONSUMER_GROUP,
        )

        # Gate + readiness round-trip (re-produce seed_gate until handle answers).
        def _produce_gate() -> None:
            msg = topic.serialize(key=GATE_KEY, value={"type": "seed_gate", "experiment": GATE_KEY})
            with app.get_producer() as producer:
                producer.produce(topic=topic.name, key=msg.key, value=msg.value)

        if wait_for_seed_gate(_produce_gate):
            logger.info("boot-seed: gate answered seeded=%s", _GATE_RESULT.get("seeded"))
        else:
            logger.warning(
                "boot-seed: gate round-trip timed out; proceeding as NOT seeded "
                "(idempotent fold + later boot are the safety nets)"
            )

        if _GATE_RESULT.get("seeded"):
            logger.info(
                "boot: cg=%s -> WARM (State seeded flag set) -> skipping lake, "
                "trusting State + topic",
                CONSUMER_GROUP,
            )
            return False
        logger.info("boot: cg=%s -> COLD (State seeded flag absent)", CONSUMER_GROUP)

        if not LAKE_URL:
            logger.warning(
                "boot-seed skipped: no Lakehouse Query URL configured "
                "(Quix__Lakehouse__Query__Url / LAKE_API_URL); live laps will fill State"
            )
            return False

        sql = build_reconcile_sql(LAKE_TABLE, BEST_COL)
        logger.info("boot-seed: lake scan SQL: %s", sql)
        rows = query_lake_with_retry(sql)
        if rows is None:
            return False  # retries exhausted; already logged
        if not rows:
            logger.info("boot-seed: lake scan returned 0 rows; nothing to seed")
            return False

        reduced = reduce_rows(rows)
        messages = build_seed_messages(reduced)
        if not messages:
            logger.info("boot-seed: no experiment-keyed rows after reduction; skipping")
            return False

        # Consumer is confirmed live (gate round-trip above), so the seed + the
        # mark_seeded write are guaranteed to land after the consumer's position.
        with app.get_producer() as producer:
            for message in messages:
                kafka_msg = topic.serialize(key=message["experiment"], value=message)
                producer.produce(topic=topic.name, key=kafka_msg.key, value=kafka_msg.value)
            gate_msg = topic.serialize(key=GATE_KEY, value={"type": "mark_seeded", "experiment": GATE_KEY})
            producer.produce(topic=topic.name, key=gate_msg.key, value=gate_msg.value)
        logger.info(
            "boot-seed: %d lake groups across %d experiments -> produced %d seed "
            "messages + mark_seeded to %s",
            len(reduced),
            len(messages),
            len(messages),
            topic.name,
        )
        return True
    except Exception:  # boot seed must NEVER crash startup
        logger.warning("boot-seed failed; State left un-seeded (retryable)", exc_info=True)
        return False


# --------------------------------------------------------------------------- #
# Config service: fetch content from the prod-edge DCM, not the empty in-cluster
# one. NOTE: this overrides QuixConfigurationService._fetch_version_content, a
# PRIVATE QS method (verified against the installed quixstreams==3.24.* source:
# `_fetch_version_content(self, version) -> Optional[bytes]`, fetching
# version.contentUrl via self._client). If a future QS release renames/reworks
# it, enrichment silently returns None again — re-verify on QS upgrades.
# --------------------------------------------------------------------------- #
class ProdDCMConfigurationService(QuixConfigurationService):
    """Fetch config content from CONFIG_API_URL (prod DCM) instead of the
    in-cluster contentUrl the byox DCM stamps (which points at an empty DCM).
    Env-driven via ``content_base``; falsy base -> native behavior unchanged."""

    def __init__(self, *args, content_base: str | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._content_base = content_base
        # prod edge is self-signed -> verify=False; preserve the Bearer/User-Agent
        # headers the base client set up from the SDK token.
        self._client = httpx.Client(
            follow_redirects=True, verify=False, headers=self._client.headers
        )

    def _fetch_version_content(self, version):  # -> Optional[bytes] (base contract)
        url = rewrite_content_url(version.contentUrl, self._content_base)
        # Rare path (lookup caches per version) -> log every call, no throttle.
        logger.info("DCM fetch: orig contentUrl=%s -> rewritten=%s", version.contentUrl, url)
        try:
            r = self._client.get(url, timeout=self._request_timeout)
            r.raise_for_status()
            logger.info(
                "DCM resp: status=%s len=%s body[:300]=%r",
                r.status_code,
                len(r.content),
                r.text[:300],
            )
            return r.content
        except Exception as exc:  # a DCM hiccup must not crash enrichment — return None like the base
            logger.warning("DCM fetch FAILED url=%s err=%r", url, exc)
            return None


# --------------------------------------------------------------------------- #
# QuixStreams app + topics + SDF branches
# --------------------------------------------------------------------------- #
app = Application(
    broker_address=os.environ.get("BROKER_ADDRESS") or None,
    consumer_group=CONSUMER_GROUP,
    auto_offset_reset="latest",  # trust committed offsets; history comes from the boot seed
    state_dir=STATE_DIR,
)

raw_topic = app.topic(
    os.environ.get("output", "ac-telemetry-raw"),
    value_deserializer="json",
    key_deserializer="str",  # join_lookup matches config by sha1(type-key); a bytes key never matches
)
session_topic = app.topic(
    os.environ.get("session_output", "ac-telemetry-session"),
    value_deserializer="json",
    key_deserializer="str",  # keep SESSION_BY_HOST keyed by the same str key shape() looks up
)
config_topic = app.topic(os.environ.get("config_input", "ac-telemetry-config"))
# One internal events topic both the raw branch and the boot seeder produce to,
# consumed by the single stateful SDF. Experiment-keyed (str) so all events for an
# experiment co-partition onto one RocksDB store.
events_topic = app.topic(
    EVENTS_TOPIC,
    value_deserializer="json",
    value_serializer="json",
    key_deserializer="str",
    key_serializer="str",
)
best_time_topic = app.topic(
    os.environ.get("best_time_output", "ac-best-laps"), value_serializer="json"
)
event_topic = app.topic(
    os.environ.get("event_output", "ac-best-laps-events"), value_serializer="json"
)

# DCM enrichment: experiment / driver / environment ONLY (track/car now from session).
# Content fetched from the prod-edge DCM (CONFIG_API_URL), not the empty in-cluster one.
lookup = ProdDCMConfigurationService(
    config_topic,
    app_config=app.config,
    fallback="default",
    content_base=DCM_CONTENT_BASE,
)
fields = {
    "experiment": lookup.json_field("$.experiment_id", type="experiment", default=""),
    "driver": lookup.json_field("$.driver", type="experiment", default=""),
    "environment": lookup.json_field("$.environment", type="experiment", default=""),
}

# Branch 1 — session: keep latest {track, carModel, session_id} per host.
app.dataframe(session_topic).update(remember_session, metadata=True)

# Branch 2 — raw -> events: enrich -> shape -> validate -> re-key -> produce a
# "lap" event to the internal events topic (the to_topic key re-partitions by
# experiment, so all events for an experiment land on one RocksDB store).
raw_sdf = app.dataframe(raw_topic).join_lookup(lookup, fields)
raw_sdf = raw_sdf.apply(shape, metadata=True)
raw_sdf = raw_sdf.filter(is_valid)
raw_sdf = raw_sdf.group_by("experiment")
raw_sdf = raw_sdf.apply(tag_lap)
raw_sdf.to_topic(events_topic, key=lambda v: v["experiment"])

# Branch 3 — events -> State: the ONE stateful op. Consumes lap + seed events,
# folds into state["board"], re-projects RAM every message; on a new best emits
# to the two output topics (seed events never set _changed).
events_sdf = app.dataframe(events_topic).apply(handle, stateful=True, metadata=True)
changed = events_sdf.filter(lambda v: v["_changed"])
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
        "boot: raw=%s session=%s config=%s events=%s best_time=%s event=%s "
        "lake_table=%s best_col=%s http=%s:%d cg=%s state_dir=%s offset=latest lake_url=%s",
        raw_topic.name,
        session_topic.name,
        config_topic.name,
        events_topic.name,
        best_time_topic.name,
        event_topic.name,
        LAKE_TABLE,
        BEST_COL,
        HTTP_HOST,
        HTTP_PORT,
        CONSUMER_GROUP,
        STATE_DIR,
        "yes" if LAKE_URL else "no",
    )
    logger.info("DCM content base (CONFIG_API_URL) = %r", DCM_CONTENT_BASE)
    # uvicorn on a daemon thread; boot seeder on another daemon thread; app.run()
    # on the MAIN thread (it owns SIGINT/SIGTERM). The seeder gates on State-dir
    # emptiness and produces seed messages the stateful SDF folds once consuming.
    http_thread = threading.Thread(target=_serve_http, name="http-server", daemon=True)
    http_thread.start()
    boot_seed_thread = threading.Thread(target=run_boot_seed, name="boot-seed", daemon=True)
    boot_seed_thread.start()
    logger.info(
        "app.run() on MAIN thread; uvicorn on daemon '%s'; boot-seed on daemon '%s'",
        http_thread.name,
        boot_seed_thread.name,
    )
    app.run()  # blocking, MAIN thread; installs SIGINT/SIGTERM
