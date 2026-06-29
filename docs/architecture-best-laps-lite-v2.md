# Architecture — best-laps-lite (State-as-truth cache + cold-start boot seed)

## What this is

A single-file QuixStreams (QS 3.24) leaderboard cache. **RocksDB State is the
ground truth; the in-process `BOARD_RAM` dict is a projection** re-built from
State on every consumed message (RAM never leads State). An inline FastAPI GET
serves the board from `BOARD_RAM` (csv/json/nested, never reading State
off-thread). `carModel`/`track` come from the **session topic**; the DCM
`join_lookup` supplies `experiment`/`driver`/`environment` only.

The ingest uses an **events-topic indirection**: the raw branch produces enriched
`{type:"lap", …}` records to an internal experiment-keyed topic
(`best-laps-events`), and a **single stateful SDF** consumes that one topic and
folds into State. This single-store funnel is what lets a **cold-start boot
seeder** put *all* experiments — including never-driven ones — into durable State
at boot: it produces `{type:"seed", …}` messages the same stateful op folds
in-context (State is writable only inside the SDF context for the message's key,
so a worker thread cannot write it directly). The seeder gates on **on-disk
State-dir emptiness**: cold → one aggregated `MIN`/`GROUP BY` lake query
(OOM-safe) → seed messages; warm → skip the lake, trust State + the topic tail.
`auto_offset_reset="latest"`: history from the seed, live laps from the tail.

Two output topics (`ac-best-laps` snapshot, `ac-best-laps-events` rich event) are
emitted only on a new/improved best. Threading mirrors `best-laps-cache`:
`app.run()` on the main thread; uvicorn and the boot seeder on worker daemon
threads. The whole service is `best-laps-lite/main.py` (~600 lines). It has no
domain classes; the one class is a thin `QuixConfigurationService` subclass
(`ProdDCMConfigurationService`) that overrides one private method to redirect DCM
content fetches to the prod-edge DCM (see "DCM content-URL rewrite" below).

Built to `dev-planning/best-laps-lite-bootseed/spec.md` (boot-seed) on top of the
v2 RAM-mirror base (`dev-planning/best-laps-lite-v2/spec.md`).

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
- **Events-topic indirection + one stateful store.** The raw branch enriches,
  validates, re-keys by experiment, tags `type:"lap"`, and produces to ONE
  internal topic (`best-laps-events`); a single `apply(handle, stateful=True)`
  consumes it. `group_by` mints a new `stream_id` per branch, so two branches
  grouping by the same key do NOT share a RocksDB store — funnelling raw laps and
  boot-seed messages through one experiment-keyed topic gives **one `stream_id` →
  one store**, which is precisely why the boot seeder can populate State for all
  experiments by producing messages the single op folds in-context. (This is the
  `quix-rocksdb-state-api` skill's multi-input→one-events-topic pattern.)
- **State is ground truth; RAM is re-projected on EVERY message.** `handle` reads
  `state["board"]` and, on every consumed message (lap or seed, changed or not),
  re-projects it into `BOARD_RAM[exp]`/`EXP_ENV[exp]` under `_RAM_LOCK` as a deep
  copy. RAM never leads State. This guarantees a cold seed surfaces in RAM on the
  seed message itself (no live tick needed) and RAM re-warms from durable State on
  the first message after a warm restart. (v2's earlier "mirror only on
  change-or-cold" optimization is dropped in favour of this stricter invariant;
  the per-message deep copy is the cost of State-as-truth.) Dedupe is inherent in
  the min-fold (`_fold` reports no change for a slower/equal lap), so there is no
  separate stateful dedupe op and no State write on a non-best lap.
- **Thread-safety of the RAM mirror.** The SDF main thread mutates the nested
  `board` in `_fold` while the uvicorn daemon thread serializes `BOARD_RAM` for a
  GET. Two guards close the race: (1) `handle` publishes a **deep copy** of the
  board under `_RAM_LOCK` (a `threading.Lock`) via `_project_ram`, so the stored
  mirror is a distinct object the SDF never mutates in place afterward; (2) every
  GET handler takes a snapshot under the same lock (`copy.deepcopy(BOARD_RAM)` /
  `dict(EXP_ENV)`) before building its response. Without this, a coincident GET
  could raise `RuntimeError: dictionary changed size during iteration`
  (intermittent 500).
- **Cold-start boot seed (gate = on-disk State emptiness).** A worker daemon
  thread, started before `app.run()`, probes the State dir for real RocksDB
  content. **Warm** (content present) → skip the lake entirely. **Cold** → run
  ONE aggregated `MIN`/`GROUP BY` query over all experiments (not per-experiment,
  not a raw 50 Hz scan — OOM-safe; no CTE per `feedback_quixlake_no_cte`), reduce
  to best-per-driver, group by experiment, and produce one `{type:"seed", …}`
  message per experiment to `best-laps-events`. `handle` folds each into
  `state["board"]` idempotently (min-update never clobbers a populated/faster
  value), which is the durable write. A lake timeout retries 2–3× @ ~60 s backoff,
  then fail-soft (WARN, leave un-seeded — a later boot retries while the volume is
  still cold); the boot thread never crashes startup. No `seed_gate`/`mark_seeded`
  round-trip and no request bridge — the on-disk probe is the gate and the
  idempotent fold is the double-seed safety net.
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

These choices implement `dev-planning/best-laps-lite-bootseed/spec.md` (boot seed,
events-topic ingest, State-as-truth) on top of the v2 RAM-mirror base
(`dev-planning/best-laps-lite-v2/spec.md` §1a).

## Data flow

```
ac-telemetry-session ─► app.dataframe(session_topic)
                          .update(remember_session, metadata=True)
                          └─► SESSION_BY_HOST[host] = {track, carModel, session_id}   (module dict)

ac-telemetry-config ──► ProdDCMConfigurationService(config_topic) ──┐ (exp/driver/env, content via CONFIG_API_URL)
                                                                    ▼
ac-telemetry-raw ─► app.dataframe(raw_topic)
                     .join_lookup(lookup, fields)          # DCM enrich (exp/driver/env)
                     .apply(shape, metadata=True)          # merge track/carModel/session_id from SESSION_BY_HOST[key]
                     .filter(is_valid)                     # exp/track/car/driver non-empty AND 0 < iBestTime < INT_MAX
                     .group_by("experiment")               # re-key by experiment
                     .apply(tag_lap)                       # value["type"]="lap"
                     .to_topic(best-laps-events, key=experiment)
                                          │
   boot-seed (daemon thread) ────────────┤  cold only: build_reconcile_sql (MIN/GROUP BY, all exps)
   gate: state_has_rocksdb_content(...)  │  -> query_lake_with_retry -> reduce_rows -> build_seed_messages
   cold -> produce per-exp seed msgs ────┤  -> producer.produce({type:"seed", experiment, environment, rows})
                                          ▼
best-laps-events ─► app.dataframe(events_topic).apply(handle, stateful=True, metadata=True)
                          │  handle(value, key, ts, headers, state):   # State keyed by experiment
                          │    board = state.get("board") or {}
                          │    if type=="seed": for r in rows: _fold(board, r)   # idempotent min-update
                          │                     if any folded: state.set("board", board)   # no emit
                          │    else (type=="lap"): changed, prev = _fold(board, value)
                          │                     if changed: state.set("board", board); annotate _changed/_board/_previous_ms/_timestamp_ms
                          │    _project_ram(exp, board, env)   # EVERY message: with _RAM_LOCK BOARD_RAM[exp]=deepcopy(board)
                          ▼
                     sdf.filter(v["_changed"])              # seeds never set _changed
                          ├─► .apply(to_best_time_payload).to_topic(ac-best-laps,        key=experiment)
                          └─► .apply(to_event_payload).to_topic(ac-best-laps-events,     key=experiment)

GET /best-laps  ◄─ uvicorn (worker daemon thread) ◄─ snapshot BOARD_RAM/EXP_ENV under _RAM_LOCK (never state.get())
```

### State / value shapes

- **State** (keyed by `experiment`, one store via the events topic): `board =
  {track: {carModel: {driver: best_ms}}}`. `best_ms` is integer milliseconds
  (`iBestTime`); INT_MAX (2147483647) and `<=0` are never stored. **There is no
  `seeded` State flag** — the "have we seeded?" signal is on-disk State-dir
  emptiness; a populated board IS the durable record.
- **`BOARD_RAM`**: `{experiment: board}` — the RAM projection, the sole GET read
  source, re-built from State every message. **`EXP_ENV`**: `{experiment:
  environment}` — carried separately so rows-mode output can emit `environment`
  (which isn't in the nested board).
- **`SESSION_BY_HOST`**: `{hostname: {track, carModel, session_id}}`.
- **`best-laps-events` (internal topic):** `{type:"lap", experiment, environment,
  track, carModel, driver, iBestTime, session_id?}` from the raw branch;
  `{type:"seed", experiment, environment, rows:[{track, carModel, driver,
  best_lap_ms}]}` from the boot seeder. JSON value, `str` key (experiment).

### `_fold` contract

`_fold(board, row) -> (changed: bool, previous_ms: int | None)`:
- first insert for a driver → `(True, None)` (drives `first_for_driver = True`,
  `delta_ms = None`);
- strict improvement → `(True, old_ms)` (`delta_ms = best - old`);
- slower / equal / INT_MAX / blank / non-int → `(False, current_or_None)`, no
  write.

### HTTP read modes

`GET /best-laps` is a **drop-in replica of `best-laps-cache`'s `/best-laps`
contract** so the Telemetry Dashboard's `/leaderboard` → GET path works against
lite v2 unchanged. Same params, same default, same CSV columns/order.

- Query params: `environment` (accepted, **not** a filter — single-env service),
  `experiment`, `track`, `carModel` (camelCase), `driver` (accepted; filters only
  if provided), `format` (default **`csv`**).
- **Default → `text/csv`** (`PlainTextResponse`), columns in EXACT order
  `environment,experiment,track,carModel,driver,iBestTime` (header row + rows),
  via the local `_CSV_COLUMNS` / `_to_csv` mirroring the cache. Rows sorted by
  `(track, carModel, iBestTime)` fastest-first (matching the cache's sort).
- `?format=json` → the cache's envelope `{table: <LAKE_TABLE>, columns: [...],
  rows: [...], row_count: N, source: "best-laps-lite", as_of_epoch: <ts>}`.
- `?format=nested` → the original nested mode (`{boards|board, experiments,
  as_of_epoch, source: "best-laps-lite-ram"}`), kept available but **not** the
  default.
- **Target experiment:** the `experiment` param selects that board; **omitted ⇒
  ALL experiments** flattened (lite has no single "active experiment" like the
  cache, so omitted means every board in `BOARD_RAM`).
- Rows are built from the locked RAM snapshot via `to_rows(boards, envs)` and
  then filtered by `track` / `carModel` / `driver`.
- `GET /healthz` → `{status, experiments, boards}` (also snapshots under the lock).
- Empty/not-warm or unknown experiment → 200 with the CSV **header only** (or an
  empty `rows`/board in json/nested), never a 4xx/5xx.

## Threading & shutdown

`app.run()` installs SIGINT/SIGTERM handlers via `signal.signal`, which only
works on the main thread, so it runs **blocking on the main thread**. Two worker
**daemon** threads start before it: uvicorn (HTTP) and `run_boot_seed` (cold-start
seed). Off the main thread uvicorn's `capture_signals` is a no-op, so there is no
signal clash. On SIGTERM, `app.run()` returns and the process exits, tearing down
both daemon threads. The boot seeder produces seed messages and exits; producing
onto `best-laps-events` before `app.run()` is fully consuming is safe (messages
persist until consumed).

## Cold-start boot seed (State-dir gate, aggregated query, retry)

The boot seeder (`run_boot_seed`, worker daemon thread) decides cold vs warm by
**probing the on-disk State directory** for real RocksDB content, then either
seeds State for all experiments or skips the lake.

**State-dir probe (`state_has_rocksdb_content`, verified against installed
`quixstreams==3.24.*`).** QS lays State out as
`<Quix__State__Dir>/<consumer_group>/<store>/<stream_id>/<partition>/`:
`StateStoreManager.__init__` appends `group_id` to the state dir; `RocksDBStore`
appends the store name (default `"default"`), then the `stream_id` (the events
topic name), then the integer partition. A RocksDB partition that has been opened
or committed always contains a `CURRENT` file (and `MANIFEST-*` / `*.sst` once
data is flushed). The probe walks the `<state_dir>/<consumer_group>` subtree
(falling back to the whole state dir) and reports **warm** only when it finds a
`CURRENT` / `MANIFEST-*` / `*.sst` marker — **not** on bare directory existence,
so a process that created the dir but never committed still counts as **cold**
(re-seedable). Verified empirically: opening a `rocksdict.Rdict` writes `CURRENT`,
`MANIFEST-*`, `IDENTITY`, `*.sst`, `LOG`, `LOCK`. `LOG`/`LOCK` alone are not
treated as a commit marker.

**Aggregated query (OOM-safe).** On cold, `build_reconcile_sql` emits a
single-level `MIN`/`GROUP BY` over all experiments —
`SELECT environment, experiment, track, carModel, driver, MIN(iBestTime) AS
iBestTime FROM <LAKE_TABLE> WHERE iBestTime>0 AND iBestTime<INT_MAX GROUP BY
environment, experiment, track, carModel, driver` — so the lake returns ≤1 row per
driver group, not every 50 Hz tick. No CTE (`feedback_quixlake_no_cte`); the old
per-experiment raw scan is **deleted**. It is POSTed to `{LAKE_URL}/query` with
`verify=False` (byox self-signed) and a Bearer token if set; `reduce_rows`
collapses to `{(env,exp,track,car,driver): min_ms}`, `build_seed_messages` groups
by experiment into one `{type:"seed", …}` per experiment, produced via
`events_topic.serialize` + `app.get_producer()`.

**Retry / fail-soft.** `query_lake_with_retry` retries transport/timeout errors
up to 3× with ~60 s backoff (`feedback_quixlake_aggregation_slow`), then returns
`None` — the seeder logs a WARNING and leaves State un-seeded so a later boot
retries while the volume is still cold. An empty result (0 rows) is logged and
skipped. The boot thread never crashes startup.

On **byox**, `Quix__Lakehouse__Query__Url` / `Quix__Lakehouse__Query__AuthToken`
are **not** auto-injected, so they are declared in `app.yaml`; the code also
accepts the legacy `LAKE_API_URL` / `LAKE_API_TOKEN` fallbacks. If neither is
set, the seed is skipped (lake URL absent) and live laps fill State.

## Accepted residual (warm restart, never-driven experiment)

A seeded-but-never-driven experiment lives in **State** (durable across restarts)
but is **absent from `BOARD_RAM` after a *warm* restart** until a message for it
arrives — RAM re-warms on traffic. After a *cold* seed the seed message itself
re-projects the board into RAM (no live tick needed), so the residual only bites
on a warm restart for experiments with zero live laps since boot. Acceptable:
State holds the durable record and GET re-warms naturally. Eliminating it would
require replaying State into RAM at boot, which State's in-context-only access
forbids without a full re-consume (out of scope).

## File inventory

| File | Change | Why |
|------|--------|-----|
| `best-laps-lite/main.py` | Rewritten | Single file: session branch + raw→`best-laps-events` producer (`tag_lap`) + one stateful `handle` consuming the events topic; State-as-truth with RAM re-projected every message under `_RAM_LOCK`; cold-start `run_boot_seed` (daemon thread) gated by `state_has_rocksdb_content`, aggregated `build_reconcile_sql` + `query_lake_with_retry` + `reduce_rows`/`build_seed_messages`; `auto_offset_reset="latest"`; inline FastAPI csv/json/nested GET; `ProdDCMConfigurationService` redirecting DCM content to `CONFIG_API_URL`; diagnostic logging. Removed the per-experiment `query_lake` lazy seed and the `seeded` State flag. |
| `best-laps-lite/app.yaml` | Modified | Added `events_topic` (FreeText, default `best-laps-events`) alongside the prior `best_time_output`/`event_output`/`session_output`/byox lake var declarations. |
| `best-laps-lite/requirements.txt` | Modified | Pinned `quixstreams==3.24.*`; `fastapi`, `uvicorn[standard]`, `httpx` unchanged. |
| `best-laps-lite/dockerfile` | Unchanged | python:3.13-slim, EXPOSE 80, `python main.py`. |

## Integration with neighbouring features

- **Telemetry Dashboard** consumes `GET /best-laps` as a **drop-in replica of
  best-laps-cache's endpoint**: default `text/csv` with columns
  `environment,experiment,track,carModel,driver,iBestTime`, the same
  `environment`/`experiment`/`track`/`carModel`/`driver`/`format` params, and a
  `?format=json` envelope — so the dashboard's `/leaderboard` → GET path works
  against v2 unchanged. (`?format=nested` exposes the RAM-native nested board for
  other consumers.)
- **DCM enrichment** uses the same `join_lookup` + `QuixConfigurationService`
  idiom as `ac-telemetry-lake` and v1, keyed by `hostname == target_key`, but
  resolves only `experiment`/`driver`/`environment` (no `type="session"`
  fields). See memory `reference_quixstreams_config_lookup`.
- **DCM content-URL rewrite (byox).** Each config event carries a `contentUrl`
  the native `QuixConfigurationService` fetches verbatim. On byox the in-cluster
  DCM stamps `contentUrl=http://dynamic-configuration-manager`, which is an
  **empty** DCM — so enrichment returned blank `experiment`/`driver`, every raw
  tick failed `is_valid`, and State never populated (observed: `/best-laps` 0
  rows, `/healthz` boards=0). The real configs live on the prod-edge DCM, which
  the service already has as the `CONFIG_API_URL` env var. `ProdDCMConfigurationService`
  subclasses the lookup and overrides the private `_fetch_version_content` to
  fetch from a URL rewritten by `rewrite_content_url(version.contentUrl,
  CONFIG_API_URL)` — it swaps scheme+host to the prod-edge base and keeps
  path/query/fragment. The override rebuilds the httpx client with `verify=False`
  (prod edge is self-signed) while preserving the base's Bearer/User-Agent
  headers, never mutates the (frozen) `version`, and returns `None` on failure
  like the base. If `CONFIG_API_URL` is falsy, `rewrite_content_url` is a no-op,
  so other envs where the in-cluster `contentUrl` is correct are unaffected.
  **Risk:** `_fetch_version_content` is a private QS method; verified against the
  installed `quixstreams==3.24.*` source — re-verify on any QS upgrade, since a
  rename would silently revert enrichment to fetching the empty in-cluster URL.
- **Session topic** (`ac-telemetry-session`, emitted by `ac-telemetry-source` on
  session change) is now an input, supplying track/carModel/session_id. Because
  AC publishes session on session change *before* any lap, the session normally
  arrives before a host's first raw tick; a raw tick that beats its session is
  dropped by `is_valid` (blank track/car) until a session lands (spec §4.6).
- **State / changelog / deployment** follow the `quix-rocksdb-state-api` skill:
  `state: enabled: true`, `network.serviceName`, and the `best-laps-events` topic
  wiring belong in `quix.yaml` (not touched here — owned by Buddy/deployment). The
  events-topic→single-stateful-store funnel is exactly that skill's
  multi-input→one-events-topic pattern; the persistent RAM mirror (read path) is a
  deliberate departure from its "no persistent RAM view" rule, mandated by the
  spec's State-as-truth/RAM-projection design. State remains the durable store and
  the changelog topic still proves persistence.

## Constraints honoured

Single file, QS 3.24 primitives only, no domain classes — the sole class is the
thin `ProdDCMConfigurationService` lookup subclass (one overridden private
method). Raw HTTP is confined to three places: the boot-seed lake `httpx` query,
the DCM content fetch in `ProdDCMConfigurationService._fetch_version_content`
(both `verify=False` against byox self-signed edges), and the inline FastAPI GET
server. Everything else is native QS (`Application`, `apply`/`update`/`filter`,
`State`, `join_lookup` + `QuixConfigurationService`, `group_by`, `to_topic`, and
the boot seeder's `app.get_producer()` + `events_topic.serialize` — not a
hand-rolled Kafka client). The seed query is aggregated (`MIN`/`GROUP BY`,
OOM-safe); no per-experiment raw scan. The HTTP thread never calls `state.get()`.
`ruff check` passes with default rules.
