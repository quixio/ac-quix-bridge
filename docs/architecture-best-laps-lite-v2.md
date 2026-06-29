# Architecture — best-laps-lite v2 (RAM-mirror cache)

## What this is

A rewrite of the `best-laps-lite` service into a single-file, class-free
QuixStreams (QS 3.24) leaderboard cache. It maintains per-experiment best laps
in **RocksDB State**, mirrors that State board into an **in-process RAM dict**
(`BOARD_RAM`), serves the board over **HTTP GET** (read straight from RAM, never
from State off-thread), and **emits to two output topics** on a new/improved
best. State cold-starts from the LakeHouse when empty. Unlike v1, `carModel` and
`track` are sourced from the **session topic** (`ac-telemetry-session`), not from
DCM; the DCM `join_lookup` now supplies `experiment` / `driver` / `environment`
only. Threading mirrors `best-laps-cache`: `app.run()` owns the main thread,
uvicorn runs on a worker daemon thread. The whole service is
`best-laps-lite/main.py` (~340 lines, no classes).

## Why this design

- **RAM mirror instead of a per-request State round-trip.** `best-laps-cache`
  serves reads by producing a synthetic `get_request` event, reading State
  in-context inside the SDF, and handing the payload back through a
  `PendingRequests`/`threading.Event` bridge (because RocksDB State is reachable
  *only* inside a stateful SDF op for that message's key — never off-thread).
  v2 trades that machinery for a persistent module-level dict that the HTTP
  thread reads directly. Simpler, no round-trip latency, no bridge. The cost is
  a second copy of the board in RAM and a re-hydration concern after restart
  (handled below). State remains the durable source of truth; RAM is a
  read-optimized projection.
- **Every raw tick reaches `handle` (no `is_new_best` pre-filter).** v1 had a
  `filter(is_new_best, stateful=True)` before `group_by` that dropped non-best
  ticks so they never reached the fold. v2 **removes that filter entirely**.
  Every raw tick is `group_by("experiment")`'d and reaches `handle`, which
  reads `board` from State, folds the tick, and publishes the board into
  `BOARD_RAM[experiment]` whenever the content **changed OR RAM is still cold**
  for that experiment. The "cold" clause re-hydrates RAM from durable State on
  the **first raw tick of any kind** after a restart with retained State (not
  only after the first new best), while avoiding a deep copy on every non-best
  tick once warm. Dedupe is now inherent in the min-fold (`_fold` reports no
  change for a slower/equal lap), so the separate stateful dedupe op is redundant.
- **Thread-safety of the RAM mirror.** The SDF main thread mutates the nested
  `board` in `_fold` while the uvicorn daemon thread serializes `BOARD_RAM` for a
  GET. Two guards close the race: (1) `handle` publishes a **deep copy** of the
  board under `_RAM_LOCK` (a `threading.Lock`), so the stored mirror is a
  distinct object the SDF never mutates in place afterward; (2) every GET handler
  takes a snapshot under the same lock (`copy.deepcopy(BOARD_RAM)` / `dict(EXP_ENV)`)
  before building its response, so it never iterates the live dicts. Without this,
  a coincident GET could raise `RuntimeError: dictionary changed size during
  iteration` (intermittent 500).
- **track/carModel from the session topic, not DCM.** The live session document
  is more authoritative for the *active* car/track than a DCM `type="session"`
  config document. v1's two DCM `json_field(type="session")` lookups are gone; a
  module-level `SESSION_BY_HOST[hostname]` dict (latest-wins, fed by the session
  branch) supplies them at `shape` time.
- **Two output topics.** `ac-best-laps` carries a full board snapshot per
  experiment (so a consumer can render the whole leaderboard from one message);
  `ac-best-laps-events` carries one rich event per new best (`previous_best_ms`,
  `delta_ms`, `first_for_driver`, `session_id`) for notification/animation
  consumers. Both are emitted only on a change.

These choices implement the resolved decisions in
`dev-planning/best-laps-lite-v2/spec.md` §1a, which override the §8 open
questions.

## Data flow

```
ac-telemetry-session ─► app.dataframe(session_topic)
                          .update(remember_session, metadata=True)
                          └─► SESSION_BY_HOST[host] = {track, carModel, session_id}   (module dict)

ac-telemetry-config ──► QuixConfigurationService(config_topic) ──┐ (experiment/driver/environment)
                                                                 ▼
ac-telemetry-raw ─► app.dataframe(raw_topic)
                     .join_lookup(lookup, fields)          # DCM enrich (exp/driver/env)
                     .apply(shape, metadata=True)          # merge track/carModel/session_id from SESSION_BY_HOST[key]
                     .filter(is_valid)                     # exp/track/car/driver non-empty AND 0 < iBestTime < INT_MAX
                     .group_by("experiment")               # re-key: State + fold are per experiment
                     .apply(handle, stateful=True, metadata=True)
                          │
                          │  handle(value, state, key, ts, headers):
                          │    board = state.get("board") or {}
                          │    if not seeded: fold query_lake(exp); set seeded,board   # one-time lake cold-start
                          │    changed, previous_ms = _fold(board, value)               # min-update; dedupe inherent
                          │    if changed: state.set("board", board)                    # durable write only on change
                          │    annotate value with _changed/_board/_previous_ms/_timestamp_ms
                          │    if changed or exp not in BOARD_RAM:                       # publish gated by changed-or-cold
                          │        with _RAM_LOCK: BOARD_RAM[exp]=deepcopy(board); EXP_ENV[exp]=env   # re-hydrate, no aliasing
                          ▼
                     sdf.filter(v["_changed"])
                          ├─► .apply(to_best_time_payload).to_topic(ac-best-laps,        key=experiment)
                          └─► .apply(to_event_payload).to_topic(ac-best-laps-events,     key=experiment)

GET /best-laps  ◄─ uvicorn (worker daemon thread) ◄─ snapshot BOARD_RAM/EXP_ENV under _RAM_LOCK (never state.get())
```

### State / value shapes

- **State** (keyed by `experiment` after `group_by`): `board = {track: {carModel:
  {driver: best_ms}}}`, plus a sibling `seeded: bool` flag. `best_ms` is integer
  milliseconds (`iBestTime`); INT_MAX (2147483647) and `<=0` are never stored.
- **`BOARD_RAM`**: `{experiment: board}` — the RAM mirror, the sole GET read
  source. **`EXP_ENV`**: `{experiment: environment}` — carried separately so
  rows-mode output can emit `environment` (which isn't in the nested board).
- **`SESSION_BY_HOST`**: `{hostname: {track, carModel, session_id}}`.

### `_fold` contract

`_fold(board, row) -> (changed: bool, previous_ms: int | None)`:
- first insert for a driver → `(True, None)` (drives `first_for_driver = True`,
  `delta_ms = None`);
- strict improvement → `(True, old_ms)` (`delta_ms = best - old`);
- slower / equal / INT_MAX / blank / non-int → `(False, current_or_None)`, no
  write.

### HTTP read modes

- `GET /best-laps` → nested `{boards: {exp: {track:{car:{driver:ms}}}},
  experiments, as_of_epoch, source}`.
- `GET /best-laps?experiment=<exp>` → that single board.
- `GET /best-laps?format=rows` → flat JSON list of `{environment, experiment,
  track, carModel, driver, iBestTime}`, fastest-first within each
  track/carModel group — matches `best-laps-cache`'s row contract so the
  Telemetry Dashboard can point at v2 unchanged.
- `GET /healthz` → `{status, experiments, boards}`.
- Empty/not-warm → 200 with an empty board/list, never a 4xx/5xx.

## Threading & shutdown

`app.run()` installs SIGINT/SIGTERM handlers via `signal.signal`, which only
works on the main thread, so it runs **blocking on the main thread**. uvicorn
runs on a worker **daemon** thread; off the main thread its `capture_signals` is
a no-op, so there is no signal clash. On SIGTERM, `app.run()` returns and the
process exits, tearing down the daemon HTTP thread. There is **no boot-seed
thread** (unlike `best-laps-cache`) — seeding is lazy, gated by the `seeded`
flag, inside `handle` on the first tick per experiment partition.

## Cold-start from the LakeHouse

`query_lake(experiment)` is carried verbatim from v1: it POSTs raw SQL
(`SELECT track, carModel, driver, iBestTime FROM <LAKE_TABLE> WHERE iBestTime > 0
AND iBestTime < INT_MAX AND experiment = '<esc>'`) to `{LAKE_URL}/query` with
`verify=False` (byox self-signed cert) and a Bearer token if present, parses the
CSV response, and raises on a `# ERROR:` body. It runs once per experiment
partition, gated by `state.get("seeded")`. A lake failure is caught and logged —
the service then builds State live-only rather than crashing the fold.

On **byox**, `Quix__Lakehouse__Query__Url` / `Quix__Lakehouse__Query__AuthToken`
are **not** auto-injected (no `blobStorage.bind` on this target), so they are
declared in `app.yaml`; the code also accepts the legacy `LAKE_API_URL` /
`LAKE_API_TOKEN` fallbacks. If neither is set, the seed is skipped (lake URL
absent) and the board builds live-only.

## File inventory

| File | Change | Why |
|------|--------|-----|
| `best-laps-lite/main.py` | Rewritten | Single-file v2: session/raw/handle branches, RAM mirror, inline FastAPI, two output topics, lazy lake seed. |
| `best-laps-lite/app.yaml` | Modified | Added `best_time_output` (`ac-best-laps`) + `event_output` (`ac-best-laps-events`) OutputTopics; added `session_output` default; declared byox `Quix__Lakehouse__Query__Url`/`__AuthToken` (not auto-injected on byox); kept `output`/`config_input`/`CONFIG_API_URL`/`LAKE_TABLE`/`LAKE_COL_BEST_TIME`/`HTTP_PORT`/`CONSUMER_GROUP`/`Quix__State__Dir`. |
| `best-laps-lite/requirements.txt` | Modified | Pinned `quixstreams==3.24.*`; `fastapi`, `uvicorn[standard]`, `httpx` unchanged. |
| `best-laps-lite/dockerfile` | Unchanged | python:3.13-slim, EXPOSE 80, `python main.py`. |

## Integration with neighbouring features

- **Telemetry Dashboard** can consume `GET /best-laps?format=rows` — the flat
  row shape is column-compatible with `best-laps-cache`'s contract
  (`environment, experiment, track, carModel, driver, iBestTime`), so the
  dashboard's `/leaderboard` → GET path works against v2 unchanged.
- **DCM enrichment** uses the same `join_lookup` + `QuixConfigurationService`
  idiom as `ac-telemetry-lake` and v1, keyed by `hostname == target_key`, but
  resolves only `experiment`/`driver`/`environment` (no `type="session"`
  fields). See memory `reference_quixstreams_config_lookup`.
- **Session topic** (`ac-telemetry-session`, emitted by `ac-telemetry-source` on
  session change) is now an input, supplying track/carModel/session_id. Because
  AC publishes session on session change *before* any lap, the session normally
  arrives before a host's first raw tick; a raw tick that beats its session is
  dropped by `is_valid` (blank track/car) until a session lands (spec §4.6).
- **State / changelog / deployment** follow the `quix-rocksdb-state-api` skill:
  `state: enabled: true` and `network.serviceName` belong in `quix.yaml` (not
  touched here — owned by Buddy/deployment). The RAM-mirror read path is a
  deliberate departure from that skill's "no persistent RAM view" rule, mandated
  by the v2 architecture diagram; State is still the durable store and the
  changelog topic still proves persistence.

## Constraints honoured

Single file, no classes, QS 3.24 primitives only. The only raw HTTP is the lake
cold-start `httpx` query and the inline FastAPI GET server; everything else is
native QS (`Application`, `apply`/`update`/`filter`, `State`, `join_lookup` +
`QuixConfigurationService`, `group_by`, `to_topic`, `value_(de)serializer="json"`).
The HTTP thread never calls `state.get()`. `ruff check` passes with default
rules.
