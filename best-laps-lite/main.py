"""best-laps-lite — step 3: gated lazy seed + live raw, in RocksDB State.

Idiomatic QuixStreams: Application + State + the built-in config-service lookup.
No custom classes.

Chain: ac-telemetry-raw -> enrich (lookup) -> drop invalid -> dedupe ->
group_by(experiment) -> one stateful op. Per experiment, that op:
  * checks State; if not seeded, scans the whole lake table for this
    experiment and folds the best time per driver in (check -> query DB),
  * then folds the live tick.
State is keyed by experiment; board == {track:{car:{driver: best_ms}}}.
"""

import csv
import io
import os

import httpx
from quixstreams import Application
from quixstreams.dataframe.joins.lookups import QuixConfigurationService

INT_MAX = 2147483647  # AC "no lap set" sentinel — never store/serve it

# --- config from env ---
LAKE_URL = os.environ.get("Quix__Lakehouse__Query__Url") or os.environ.get("LAKE_API_URL")
LAKE_TOKEN = os.environ.get("Quix__Lakehouse__Query__AuthToken") or os.environ.get("LAKE_API_TOKEN")
LAKE_TABLE = os.environ.get("LAKE_TABLE", "ac_telemetry_prod")
BEST_COL = os.environ.get("LAKE_COL_BEST_TIME", "iBestTime")

# --- QuixStreams app + State store ---
app = Application(
    broker_address=os.environ.get("BROKER_ADDRESS") or None,
    consumer_group=os.environ.get("CONSUMER_GROUP", "best-laps-lite"),
    auto_offset_reset="earliest",
    state_dir=os.environ.get("Quix__State__Dir", "state"),
)
raw_topic = app.topic(os.environ.get("output", "ac-telemetry-raw"), value_deserializer="json")
config_topic = app.topic(os.environ.get("config_input", "ac-telemetry-config"))

# built-in lookup: raw ticks are keyed by hostname == DCM target_key, so the
# config service resolves experiment/driver/environment (+ track/car) per tick.
lookup = QuixConfigurationService(config_topic, app_config=app.config, fallback="default")
fields = {
    "experiment": lookup.json_field("$.experiment_id", type="experiment", default=""),
    "driver": lookup.json_field("$.driver", type="experiment", default=""),
    "environment": lookup.json_field("$.environment", type="experiment", default=""),
    "track": lookup.json_field("$.track", type="session", default=""),
    "carModel": lookup.json_field("$.carModel", type="session", default=""),
}


def query_lake(experiment):
    """Scan the whole table for one experiment -> rows (best per driver via fold)."""
    exp = experiment.replace("'", "''")
    sql = (f"SELECT track, carModel, driver, {BEST_COL} FROM {LAKE_TABLE} "
           f"WHERE {BEST_COL} > 0 AND {BEST_COL} < {INT_MAX} AND experiment = '{exp}'")
    headers = {"Content-Type": "text/plain"}
    if LAKE_TOKEN:
        headers["Authorization"] = f"Bearer {LAKE_TOKEN}"
    resp = httpx.post(f"{LAKE_URL.rstrip('/')}/query", content=sql,
                      headers=headers, timeout=30.0, verify=False)
    if resp.text.lstrip().startswith("# ERROR:"):
        raise RuntimeError(resp.text)
    return list(csv.DictReader(io.StringIO(resp.text)))


def _fold(board, row):
    """Min-update board[track][car][driver]; INT_MAX/<=0/blank -> no-op."""
    try:
        best = int(row[BEST_COL])
    except (TypeError, ValueError):
        return
    track, car, drv = row.get("track"), row.get("carModel"), row.get("driver")
    if not (track and car and drv) or not (0 < best < INT_MAX):
        return
    cur = board.get(track, {}).get(car, {}).get(drv)
    if cur is None or best < cur:
        board.setdefault(track, {}).setdefault(car, {})[drv] = best


def is_new_best(row, state):
    """Stateful (keyed by stream): pass only a strictly-faster iBestTime; drop repeats."""
    cur = row[BEST_COL]
    last = state.get("last")
    if last is not None and cur >= last:
        return False
    state.set("last", cur)
    return True


def handle(row, state):
    """Per experiment: seed from the lake once if State is empty, then fold the tick."""
    board = state.get("board") or {}
    if not state.get("seeded"):                      # check state
        for r in query_lake(row["experiment"]):      # not present -> query whole table
            _fold(board, r)                           # min-update == best per driver
        state.set("seeded", True)
    _fold(board, row)                                 # fold the live tick
    state.set("board", board)


# raw -> enrich -> shape -> drop invalid -> dedupe -> key by experiment -> fold
sdf = app.dataframe(raw_topic).join_lookup(lookup, fields)
sdf = sdf.apply(lambda v: {
    "experiment": v.get("experiment", ""),
    "track": v.get("track", ""),
    "carModel": v.get("carModel", ""),
    "driver": v.get("driver", ""),
    BEST_COL: int(v.get(BEST_COL) or 0),
})
sdf = sdf.filter(lambda v: bool(v["experiment"] and v["track"] and v["carModel"]
                                and v["driver"]) and 0 < v[BEST_COL] < INT_MAX)
sdf = sdf.filter(is_new_best, stateful=True)         # write once: drop unchanged iBestTime
sdf = sdf.group_by("experiment")                     # re-key so State is per experiment
sdf.update(handle, stateful=True)


if __name__ == "__main__":
    app.run()
