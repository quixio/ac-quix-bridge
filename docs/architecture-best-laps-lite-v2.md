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
so a worker thread cannot write it directly). The seeder gates on a
**State-native `seeded` flag** read via a round-trip that doubles as a
latest-offset readiness barrier: cold (flag absent) → one aggregated `MIN`/`GROUP
BY` lake query (OOM-safe) → seed messages → a `mark_seeded` write; warm (flag
set) → skip the lake, trust State + the topic tail.
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
- **Cold-start boot seed (authoritative gate = State-native `seeded` flag).** A
  worker daemon thread, started before `app.run()`, gates on a `seeded` flag held
  in RocksDB State at `GATE_KEY` (best-laps-cache's `GATE_KEY`/`mark_seeded`
  pattern). Because State is scoped to `<state_dir>/<consumer_group>`, the flag is
  naturally absent after a state-volume wipe OR a consumer-group change → reseed.
  **Warm** (flag set) → skip the lake. **Cold** (flag absent) → run ONE aggregated
  `MIN`/`GROUP BY` query over all experiments (not per-experiment, not a raw 50 Hz
  scan — OOM-safe; no CTE per `feedback_quixlake_no_cte`), reduce to
  best-per-driver, group by experiment, produce one `{type:"seed", …}` per
  experiment to `best-laps-events`, then a `{type:"mark_seeded"}` so `handle` sets
  the flag. `handle` folds each seed into `state["board"]` idempotently (min-update
  never clobbers a populated/faster value) — the durable write. A lake timeout
  retries 2–3× @ ~60 s backoff, then fail-soft (WARN, leave the flag unset — a
  later boot retries); the boot thread never crashes startup. The idempotent fold
  is the double-seed safety net. A cheap on-disk `state_has_rocksdb_content` check
  is logged as a hint but is **not** authoritative.
- **Readiness barrier folded into the gate (latest-offset race).** With
  `auto_offset_reset="latest"` the events consumer positions at the tail on
  assignment, so a message produced *before* assignment is skipped — a seed
  produced too early would be **silently lost**. The gate round-trip doubles as
  the barrier: `run_boot_seed` re-produces a `{type:"seed_gate"}` event keyed
  `GATE_KEY` every ~2 s (bounded ~120 s) until `handle` processes one — which
  reads the flag in-context, records it in `_GATE_RESULT`, and sets `_GATE_EVENT`.
  Re-producing defeats the race for the probe itself (an early one is missed, the
  next post-assignment one is read). Only after the event is set are the seed
  messages produced, guaranteed to land after the consumer's position. A barrier
  timeout proceeds as not-seeded (the idempotent fold is the safety net). One
  mechanism gates and confirms readiness; there is no separate ping and no request
  bridge. (Keeping `latest` avoids re-reading the huge raw history that `earliest`
  would force.)
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
   boot-seed (daemon thread)             │  gate+readiness round-trip: re-produce {type:"seed_gate"}@~2s
     wait_for_seed_gate(produce_gate) ───┤    until handle sets _GATE_EVENT (records state["seeded"])
     if _GATE_RESULT["seeded"]: skip     │  if NOT seeded: build_reconcile_sql (MIN/GROUP BY, all exps)
     else (cold) produce seeds ──────────┤    -> query_lake_with_retry -> reduce_rows -> build_seed_messages
     then {type:"mark_seeded"} ──────────┤    -> produce {type:"seed",...} per exp, then {type:"mark_seeded"}
                                          ▼
best-laps-events ─► app.dataframe(events_topic).apply(handle, stateful=True, metadata=True)
                          │  handle(value, key, ts, headers, state):   # State keyed by experiment
                          │    if type=="seed_gate": _GATE_RESULT["seeded"]=bool(state.get("seeded")); _GATE_EVENT.set(); return   # readiness+gate
                          │    if type=="mark_seeded": state.set("seeded", True); return
                          │    board = state.get("board") or {}
                          │    if type=="seed": for r in rows: _fold(board, r)   # idempotent min-update
                          │                     if any folded: state.set("board", board)   # no emit
                          │    else (type=="lap"): changed, prev = _fold(board, value)
                          │                     if changed: state.set("board", board); annotate _changed/_board/_previous_ms/_timestamp_ms
                          │    _project_ram(exp, board, env)   # seed/lap: with _RAM_LOCK BOARD_RAM[exp]=deepcopy(board)
                          ▼
                     sdf.filter(v["_changed"])              # seeds never set _changed
                          ├─► .apply(to_best_time_payload).to_topic(ac-best-laps,        key=experiment)
                          └─► .apply(to_event_payload).to_topic(ac-best-laps-events,     key=experiment)

GET /best-laps  ◄─ uvicorn (worker daemon thread) ◄─ snapshot BOARD_RAM/EXP_ENV under _RAM_LOCK (never state.get())
```

### State / value shapes

- **State** (keyed by `experiment`, one store via the events topic): real
  experiment partitions hold `board = {track: {carModel: {driver: best_ms}}}`
  (`best_ms` integer ms; INT_MAX (2147483647) and `<=0` are never stored). The
  reserved `GATE_KEY` (`"__seed_gate__"`) partition holds the `seeded` bool — the
  authoritative cold/warm gate, scoped to `<state_dir>/<consumer_group>` so a
  volume wipe or group change drops it.
- **`BOARD_RAM`**: `{experiment: board}` — the RAM projection, the sole GET read
  source, re-built from State every fold-carrying message. **`EXP_ENV`**:
  `{experiment: environment}` — carried separately so rows-mode output can emit
  `environment` (which isn't in the nested board).
- **`SESSION_BY_HOST`**: `{hostname: {track, carModel, session_id}}`.
- **Boot-gate module state:** `_GATE_EVENT` (threading.Event, set when `handle`
  answers a `seed_gate`) and `_GATE_RESULT` (`{"seeded": bool}`, the in-context
  flag read handed back to the boot thread).
- **`best-laps-events` (internal topic):** `{type:"lap", experiment, environment,
  track, carModel, driver, iBestTime, session_id?}` from the raw branch;
  `{type:"seed", experiment, environment, rows:[{track, carModel, driver,
  best_lap_ms}]}` and the gate events `{type:"seed_gate", experiment:GATE_KEY}` /
  `{type:"mark_seeded", experiment:GATE_KEY}` from the boot seeder. JSON value,
  `str` key (experiment / GATE_KEY).

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

## Cold-start boot seed (State-native gate + readiness round-trip, retry)

The boot seeder (`run_boot_seed`, worker daemon thread) decides cold vs warm via a
**State-native `seeded` flag** read through a round-trip that also serves as the
latest-offset readiness barrier, then seeds State for all experiments or skips the
lake.

**Gate + readiness round-trip (`wait_for_seed_gate`).** With
`auto_offset_reset="latest"` a message produced before the events consumer has
assigned/positioned is skipped, so the gate read and the readiness check are one
mechanism: `run_boot_seed` re-produces a `{type:"seed_gate", experiment:GATE_KEY}`
event every ~2 s (bounded ~120 s) until `handle` processes one. `handle`'s
`seed_gate` branch reads `state.get("seeded")` **in-context** (the only place
State is reachable), records it in `_GATE_RESULT`, and sets `_GATE_EVENT` — which
also proves the consumer is live. Re-producing defeats the race for the probe
itself (an early `seed_gate` is missed; the next post-assignment one is read). If
`_GATE_RESULT["seeded"]` is True → **warm**, skip the lake. Else → **cold**, seed,
then produce `{type:"mark_seeded"}` so `handle` sets the flag. A round-trip
timeout proceeds as not-seeded (idempotent fold is the safety net). The flag is
scoped to `<state_dir>/<consumer_group>`, so a volume wipe or a consumer-group
change drops it → reseed.

**On-disk probe (`state_has_rocksdb_content`) — cheap hint only, NOT
authoritative.** Logged at boot to characterise the volume. Verified against the
installed `quixstreams==3.24.*`: QS lays State out as
`<Quix__State__Dir>/<consumer_group>/<store>/<stream_id>/<partition>/`
(`StateStoreManager.__init__` appends `group_id`; `RocksDBStore` appends the store
name [default `"default"`], the `stream_id`, then the integer partition). A
RocksDB partition that has been opened/committed always contains a `CURRENT` file
(and `MANIFEST-*` / `*.sst` once flushed). The probe walks the
`<state_dir>/<consumer_group>` subtree and returns warm only on a `CURRENT` /
`MANIFEST-*` / `*.sst` marker — not bare directory existence. Verified
empirically: opening a `rocksdict.Rdict` writes `CURRENT`, `MANIFEST-*`,
`IDENTITY`, `*.sst`, `LOG`, `LOCK`; `LOG`/`LOCK` alone are not treated as a commit
marker.

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
| `best-laps-lite/main.py` | Rewritten | Single file: session branch + raw→`best-laps-events` producer (`tag_lap`) + one stateful `handle` (dispatch `seed_gate`/`mark_seeded`/`seed`/`lap`) consuming the events topic; State-as-truth with RAM re-projected every fold message under `_RAM_LOCK`; cold-start `run_boot_seed` (daemon thread) gated by a State-native `seeded` flag via `wait_for_seed_gate` (round-trip = gate + latest-offset readiness barrier), aggregated `build_reconcile_sql` + `query_lake_with_retry` + `reduce_rows`/`build_seed_messages`, `mark_seeded` write; `auto_offset_reset="latest"`; inline FastAPI csv/json/nested GET; `ProdDCMConfigurationService` redirecting DCM content to `CONFIG_API_URL`; diagnostic logging. Removed the per-experiment `query_lake` lazy seed. |
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
