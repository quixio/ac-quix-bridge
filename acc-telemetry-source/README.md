# ACC Telemetry Source

QuixStreams custom Source that reads Assetto Corsa Competizione (ACC) physics
telemetry from Windows shared memory and publishes it to Kafka.

ACC reuses AC's three shared-memory region names (`Local\acpmf_physics`,
`Local\acpmf_graphics`, `Local\acpmf_static`) with **extended** struct layouts.
See `docs/ACCSharedMemoryDocumentationV1.8.12.pdf` for the Kunos spec.

## Files

| File | Role |
|---|---|
| `main.py` | Entry point. Wires `Application` + source, calls `configure_logging()`. |
| `acc_source.py` | `AssettoCorsaCompetizioneSource` — poll loop + session-detection state machine. |
| `acc_reader.py` | Opens 3 SHM regions via `mmap` + `ctypes`, flattens structs into a JSON-friendly dict. |
| `models.py` | `ACCPhysics` / `ACCGraphics` / `ACCStatic` ctypes structs (port of the v1.8.12 SDK). |
| `docs/ACCSharedMemoryDocumentationV1.8.12.pdf` | Official Kunos ACC SDK doc. |

## Environment Variables

QuixStreams' `Application()` constructor auto-resolves cloud broker from either:

**Mode A — SDK token (recommended for Quix Cloud):**

| Variable | Description |
|---|---|
| `Quix__Sdk__Token` | SDK token from Quix Portal → workspace → Tokens. |
| `Quix__Portal__Api` | e.g. `https://portal-api.dev.quix.io` for the dev cluster. |
| `Quix__Workspace__Id` | Optional; only needed if the token grants multiple workspaces. |

**Mode B — direct broker:**

| Variable | Description |
|---|---|
| `Quix__Broker__Address` | Pre-resolved broker URL with inline SASL credentials. |

**Topics + tuning:**

| Variable | Description | Default |
|---|---|---|
| `output` | Kafka topic for physics+graphics ticks. | `acc-telemetry-raw` |
| `session_output` | Kafka topic for session metadata (1 row per session start). | `acc-telemetry-session` |
| `SAMPLE_RATE_HZ` | Polling rate. Graphics block updates at ~60 Hz; physics at ~333 Hz. | `50` |
| `LOG_LEVEL` | `INFO` / `DEBUG` / `WARNING`. Default is `INFO`. | `INFO` |

Copy `.env.example` → `.env` and fill in values.

## Channel parity with AC

The flattened payload is a **strict superset of AC's**:

- All 174 physics+graphics channel names from `ac-telemetry-source/ac_reader.py` are present in ACC output with identical spelling.
- All 46 static channel names from AC are present.
- ACC adds 95 + 3 channels not in AC (slipRatio, brakePressure, padLife, weather, MFD pressures, lap deltas, etc.).
- AC-only signals that ACC's SDK marks "not used / not shown" are still emitted as `0` / `0.0` / `""` — never `null` — so AC consumers see no schema break, just zero data.

Effect: existing AC SQL queries, dashboards, and AI agent KBs run against ACC data unmodified.

## Session detection

ACC's `status` enum is identical to AC: `0=off, 1=replay, 2=live, 3=pause`.

Telemetry ticks are only published when `status == "live"`. A new
`session_id` (UTC-iso-ms timestamp string) is assigned when either rule fires:

### Rule A — entry / resume / pause-restart

```
prev_status != "live"  AND  current_status == "live"
```

Subcases:
- `None|off|replay -> live` → always new session.
- `pause -> live` AND `iCurrentTime` dropped (backwards) → new session
  (i.e. user opened pause menu and "Restart session"; iCurrentTime reset to 0).
- `pause -> live` without iCurrentTime drop → resume, **same session_id**
  (normal Esc-menu pause).

### Rule B — in-game restart while status stayed `live`

```
prev_status == "live"  AND  current_status == "live"
AND iCurrentTime == 0
AND iLastTime == 2147483647     # ACC's INT32_MAX "no prior lap" sentinel
AND prev_iCurrentTime > 0
```

This catches ACC's **"Restart Session"** button (often mapped to a steering
wheel button) which resets lap counters without ever flipping `status`
to pause/off long enough for our poll loop to notice. Rule A alone misses
these events; Rule B classifies them correctly.

### ACC sentinel — important

ACC writes **`2147483647` (INT32_MAX)** to `iLastTime` and `iBestTime` when no
lap has been completed yet — used to distinguish "no value" from "lap 0
completed in 0 ms".

AC uses **`0`** for the same purpose. Kunos silently flipped the convention
between AC and ACC; not documented anywhere. If you ever port Rule B to the
AC source, replace `2147483647` with `0`.

## Logging

ISO-8601 UTC with millisecond precision and a `Z` suffix:

```
2026-06-02T20:20:12.475Z INFO acc_source: status: live -> pause (iCT=148270, lap=5)
2026-06-02T20:21:01.103Z INFO acc_source: status: pause -> live (iCT=148270, lap=5) — resume same session
2026-06-02T20:25:14.892Z INFO acc_source: New session 2026-06-02T20:25:14.892Z (prev=2026-06-02T19:48:01.000Z, Rule B (in-game restart: iCT 247042 -> 0, iLastTime sentinel))
```

Every status transition logs once, even when no new session fires.
Per-tick payload dumping was removed — tail the Kafka topic with
`quix-explorations/kafka/probes/probe_tail_topic.py` instead.

## SHM open behaviour

On Windows, `mmap.mmap(-1, size, "Local\\acpmf_*", ACCESS_READ)` will *create*
a zero-filled region if the named region doesn't already exist. That hides the
"ACC not running" case (source happily reads zeros) and risks size-mismatch
when ACC starts up later. To avoid this, `acc_reader._open_shm` first probes
the region via `kernel32.OpenFileMappingW(FILE_MAP_READ, ...)` and raises
`FileNotFoundError` if it doesn't exist. The poll loop catches that and retries
every 5 seconds with a clear log.

**Practical impact:** always launch ACC and enter a session **before** running
the source. If you don't, the source just retries until ACC is available.

## Running locally

Windows + ACC required (shared memory is OS-specific).

```powershell
cd acc-telemetry-source
pip install -r requirements.txt
python main.py
```

Or via the repo-root startup scripts: `startUpScript-acc.bat` /
`start-local-acc.ps1`. They launch the source + video service in separate
PowerShell windows.
