# topic-replay

Capture live AC telemetry Kafka traffic to JSONL on disk, then replay one
clean lap on infinite loop so we can debug the Ghost Lap leaderboard UI
without a running Assetto Corsa session.

The service has two modes — `capture` and `replay` — selected by either an
argparse subcommand on the command line or the `MODE` env var.

## Why this exists

Driving 2-3 laps in AC then closing the sim leaves you with no live data
stream. The leaderboard's left panel (active driver + live sector
comparison) needs continuous raw ticks plus enrichment from session and
config topics. `topic-replay` is the smallest possible thing that produces
the same byte stream the backend would otherwise see live.

## Topics

| Topic | Capture | Replay |
|-------|---------|--------|
| `ac-telemetry-raw` | subscribe (latest) | produce (looped lap window) |
| `ac-telemetry-session` | subscribe (latest) | produce (once at start + re-asserted each lap) |
| `ac-telemetry-config` | subscribe (latest) | produce (once at start) |

The lake topics are NOT replayed. The right-hand "Best Laps" panel keeps
whatever the lake currently holds.

## Capture

```powershell
# On the sim PC, with AC running:
python topic-replay/main.py capture --out C:\tmp\replay-2026-06-02
```

Drive 2 or 3 clean laps and stop with Ctrl-C. The captured rows land in:

- `C:\tmp\replay-2026-06-02\ac-telemetry-raw.jsonl`
- `C:\tmp\replay-2026-06-02\ac-telemetry-session.jsonl`
- `C:\tmp\replay-2026-06-02\ac-telemetry-config.jsonl`

`capture` refuses to start if any of these files already exist — there is
no overwrite path. Move or delete the prior capture first.

## Replay

```powershell
# From any machine pointed at the dev Quix environment:
python topic-replay/main.py replay --src C:\tmp\replay-2026-06-02
```

The script:

1. Loads all three JSONL files.
2. Scans the raw rows for two `iCurrentTime` resets (`curr < prev` and
   `prev > 5000 ms`). The half-open window between them is one complete
   lap.
3. Produces all session messages once, then all config messages once.
4. Loops the raw lap forever, sleeping
   `(t[i+1] - t[i]) / 1000 / LAP_LOOP_SPEED` between every tick. At each
   loop boundary it re-emits the latest session row per captured key.

Inspect the window before producing:

```powershell
python topic-replay/main.py replay --src C:\tmp\replay-2026-06-02 --dry-run
```

## Local mode

When no Quix Cloud workspace is available (offline laptop, no SASL creds),
the entire pipeline can be driven against a Kafka container in
`docker-compose.dev.yml`. The single switch is the `BROKER_ADDRESS` env
var: when set, `kafka_client.py` skips the portal fetch, connects
plaintext, and stops prefixing topic names with the workspace ID. When
unset, behaviour is byte-identical to cloud mode.

The compose stack publishes Kafka on two PLAINTEXT listeners:

| Listener | Address | Used by |
|----------|---------|---------|
| INTERNAL | `kafka:9092` | backend, in-network containers |
| EXTERNAL | `localhost:29092` | host-side replay / capture |

The split is required because the in-container hostname is `kafka` but the
host sees `localhost`; a single listener can't advertise both. Replay runs
on the host (sim PC), backend runs in compose — so they need different
addresses for the same broker.

Three commands to verify end-to-end:

```powershell
# 1. Bring up the local stack (Kafka, MongoDB, mock DCM, backend, frontend)
docker compose -f docker-compose.dev.yml up

# 2. Seed the mock DCM with session + experiment configs for hostname XPS.
#    Idempotent; safe to re-run. Uses the capture's session.jsonl as the
#    source of truth for track / carModel / playerName.
python mock_config_api/seed_local.py --src C:/tmp/replay-2026-06-02

# 3. Replay against the local broker. Note: localhost:29092 (EXTERNAL),
#    NOT 9092.
$env:BROKER_ADDRESS = "localhost:29092"
python topic-replay/main.py replay --src C:/tmp/replay-2026-06-02
```

Bash equivalent for step 3:

```bash
BROKER_ADDRESS=localhost:29092 python topic-replay/main.py replay --src C:/tmp/replay-2026-06-02
```

Open `http://localhost:3000/analysis?tab=leaderboard` — the active driver
row reads "Ludvík" (or `XPS`) with the gate timer ticking.

### Local-mode limitations

- **Best Laps panel stays empty.** There is no local QuixLake; the right
  side of the leaderboard shows no historical rows. The backend logs a
  warning when `_refresh_best_laps_from_settings()` can't reach the lake
  and continues without it — no crash.
- **Historicals comparison colours don't render.** Same root cause. The
  active row itself (left side) still animates correctly.
- **Config-event topic is dead-but-harmless.** Replay produces the captured
  `ac-telemetry-config` rows but they reference cloud `contentUrl`s the
  local backend can't fetch — `_handle_config_event` warns and skips. The
  seed step above inserts the equivalent configs directly into the mock
  DCM, which is the path the session-message handler actually uses.

A future Phase 3 may add a DuckDB-backed lake mock; for now it's deferred.

## Environment variables

| Name | Default | Purpose |
|------|---------|---------|
| `BROKER_ADDRESS` | (unset) | **Local mode switch.** When set & non-empty, skip the portal fetch and connect plaintext (no SASL/SSL, no workspace prefix). Typical values: `localhost:29092` from the host, `kafka:9092` from another container. |
| `Quix__Sdk__Token` | (injected) | Quix SDK auth — same value as the live AC source. Cloud mode only. |
| `Quix__Portal__Api` | (injected) | `portal-api.dev.quix.io` for dev; `portal-api.cloud.quix.io` for prod. Cloud mode only. |
| `MODE` | `replay` | `capture` or `replay`. The CLI subcommand wins when both are present. |
| `CAPTURE_DIR` | `/data/topic-replay` | Capture output / replay source directory. |
| `LAP_LOOP_SPEED` | `1.0` | Replay speed multiplier. `2.0` plays back twice as fast; `0.5` half-speed. |
| `TARGET_HOSTNAME_OVERRIDE` | (empty) | Rewrite raw + session Kafka keys to this hostname during replay. Config keys are never rewritten. |

CLI flags `--speed` and `--target-hostname` shadow the env vars when given.

## Footguns

1. **Concurrent live AC source.** If a real AC source is publishing for
   `SIMPC-01` while `topic-replay` is also publishing for `SIMPC-01`, the
   backend's `_state` cache flaps between the two producers (same
   `(track, car, driver)` tuple, two streams). Set
   `TARGET_HOSTNAME_OVERRIDE` (or pass `--target-hostname`) to publish
   under a different hostname.
2. **Config events on prod.** `ac-telemetry-config` is the DCM event
   stream. Producing old events back onto it triggers a re-fetch of
   whatever DCM currently holds at each `contentUrl`. Point this at a
   dev environment with a populated DCM rather than prod.
3. **QuixStreams version drift.** This service pins `quixstreams==3.23.6`
   verbatim. If the AC source ever upgrades to a newer SDK with a
   different header / key encoding, captures made on the new version
   must be replayed by a `topic-replay` built against the same SDK.
   Keep the pins in lockstep.

## Files

| File | Purpose |
|------|---------|
| `main.py` | argparse entry; subcommand dispatch; `MODE` env-var fallback. |
| `capture.py` | Synchronous consumer + JSONL writer. |
| `replay.py` | JSONL loader + lap-detect-and-loop producer. |
| `lap_detection.py` | `find_single_lap(raw_rows)` — pure function. |
| `app.yaml` | Quix deployment metadata. |
| `dockerfile` | Python 3.12-slim image, mirrors `ac-telemetry-source`. |
| `requirements.txt` | `quixstreams==3.23.6` + `python-dotenv`. |
