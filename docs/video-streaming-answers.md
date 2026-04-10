# AC Video Streaming — Questions & Answers

## 1. Which SW to install on AC machine? (Claude)

Minimal install — just Python packages alongside the existing `ac-telemetry-source`:

| Package | Purpose |
|---|---|
| **dxcam** | DirectX screen capture, 60+ FPS, GPU-accelerated. Best option for game capture on Windows. |
| **opencv-python** (`cv2`) | JPEG/H.264 encoding, frame resize |
| **ffmpeg** (system binary) | MP4 muxing for saved recordings. Install via `winget install ffmpeg` or download binary. |

No OBS, no heavy dependencies. `dxcam` + `ffmpeg` is the lightest viable stack.

## 2. Storing in database in blob? (Steve)

*To be answered by Steve.*

## 3. Live stream or after-race file storage? (Claude/Tomas)

Both are feasible from the same capture loop:

```
dxcam capture (60fps)
    |---> MJPEG WebSocket -> dashboard (live view)
    \---> ffmpeg pipe -> MP4 file -> blob storage (recording)
```

One capture, two outputs. The live stream is just forwarding JPEG frames over the existing WebSocket. The recording is piping those same frames to an `ffmpeg` subprocess that writes MP4. This isn't an either/or — it's a question of which to prioritize first.

*Decision pending from Tomas.*

## 4. MP4 per lap or per whole race? (Tomas/Onboarding)

*To be answered by Tomas/Onboarding.*

## 5. How to start recording with race start — is there an event? (Claude/Daniel)

**Yes.** The AC shared memory (`ACGraphics` struct in `ac-telemetry-source/models.py`) provides:

| Field | Values | Use |
|---|---|---|
| **`status`** | `0=OFF, 1=REPLAY, 2=LIVE, 3=PAUSE` | **Start recording when status transitions to LIVE** |
| **`session`** | `0=PRACTICE, 1=QUALIFY, 2=RACE, 3=HOTLAP...` | Know what type of session it is |
| **`completedLaps`** | int | Detect lap boundaries for per-lap file splitting |
| **`flag`** | `0=NONE ... 5=CHECKERED` | **Stop recording on CHECKERED flag** |
| **`iCurrentTime`** | ms | Current lap time — drops on restart |

The existing `ac-telemetry-source/ac_source.py` already implements session detection logic in `_check_session()` (line 60-92). It detects:

- **off -> live** = new session (start recording)
- **pause -> live with iCurrentTime drop** = restart (new recording)
- **status != live** = stop/pause recording

The video recorder hooks into the **same state machine** that already drives telemetry session detection. No new events needed — just reuse the `status`, `completedLaps`, and `flag` fields from `ACGraphics`.

## 6. How to identify the correct display to record? (Claude)

`dxcam` supports multi-monitor setups natively:

```python
import dxcam
# List all outputs (monitors)
dxcam.device_info()      # shows GPU adapters
dxcam.output_info()      # shows monitors with resolution and position

# Capture from a specific monitor
camera = dxcam.create(output_idx=0)   # primary display
camera = dxcam.create(output_idx=1)   # secondary display
```

Options for selecting the right display:

| Approach | How |
|---|---|
| **Config variable** | Add `VIDEO_DISPLAY_INDEX` env var (default `0`). User sets to whichever monitor AC runs on. Simplest. |
| **Auto-detect by window title** | Use `win32gui.FindWindow()` to locate the "Assetto Corsa" window, get its monitor via `MonitorFromWindow`, map back to `dxcam` output index. Fully automatic. |
| **Region capture** | If AC runs windowed, capture just the window rect instead of full screen: `camera.grab(region=(left, top, right, bottom))`. |

**Recommendation:** Start with the env var (`VIDEO_DISPLAY_INDEX`), add auto-detect later if needed. The AC machine typically has a dedicated gaming display anyway.

## 7. Store data from unfinished laps/races — on AC exit, should MP4 be stored? (Claude)

**Yes — always finalize and store the recording, even if the session is incomplete.**

The recorder detects AC exit via:
- **`status` changing from LIVE to OFF** — AC closed or returned to menu
- **Shared memory becoming unavailable** — AC process terminated (the existing `ac_source.py` already catches `FileNotFoundError` on line 103-109)

On either event, the `ffmpeg` subprocess should be gracefully closed (flush + finalize MP4 container). An incomplete race still has valuable data.

The MP4 file metadata should include:
- `completed = false` (or mark via filename, e.g. `session_incomplete.mp4`)
- `completedLaps` count at time of exit
- `reason` = `"ac_exit"` | `"disconnect"` | `"user_stop"`

This way downstream tools (Marimo notebooks, Telemetry Explorer) can filter or flag incomplete sessions but still access the footage.

## 8. When game is paused, recording should be paused as well (Claude)

**Yes — this maps directly to the existing `status` field:**

| `ACGraphics.status` | Recording action |
|---|---|
| `2` (LIVE) | Record frames |
| `3` (PAUSE) | **Pause** — stop capturing frames, keep ffmpeg pipe open |
| `1` (REPLAY) | Pause (don't record replays as live data) |
| `0` (OFF) | **Stop & finalize** MP4 |

The existing telemetry source already skips producing data when `status != "live"` (`ac_source.py` line 129). The video recorder follows the same logic — it simply stops feeding frames to ffmpeg during pause and resumes when status returns to LIVE.

This means the MP4 will have no gap/black frames during pauses — it's a continuous recording of active driving time only. The `timestamp_ms` embedded at resume will show the real-world gap, keeping sync with telemetry intact.

## 9. Should we be able to disable recording, or always on? (Claude)

**Make it configurable via environment variable:**

```
VIDEO_RECORDING_ENABLED=true     # default: true (always record)
VIDEO_LIVE_STREAM_ENABLED=true   # default: true (always stream)
```

This follows the existing pattern in the project — `SAMPLE_RATE_HZ` is already a configurable env var, and all Quix deployments use env vars for feature toggles.

Additionally, a runtime toggle could be exposed:
- **Via the Config Form UI** (already exists in the pipeline) — add a toggle to enable/disable recording without restarting the source
- **Via the Dynamic Configuration Manager** — publish a config change to `ac-telemetry-config` topic, the video source picks it up

This way recording can be disabled during practice to save storage, and enabled for races.

## 10. Timestamp synchronization with lap data (Claude)

Every telemetry message already carries:

- **`timestamp_ms`** — wall clock (`int(time.time() * 1000)`) set in `ac_source.py:136`
- **`session_id`** — UTC timestamp string, same across telemetry and video
- **`iCurrentTime`** — AC's internal lap timer (ms)
- **`completedLaps`** — lap counter

The video recorder should embed the **same `timestamp_ms` wall clock** at each frame (or at minimum at start/stop of each recording). Then correlation is:

```
video frame at timestamp_ms=1744200000000
  <-> telemetry row WHERE timestamp_ms BETWEEN 1744199999950 AND 1744200000050
```

To keep sync tight: run the video capture in the **same process** as `ac-telemetry-source`, reading from the same `time.time()` clock. If it runs as a separate process, use NTP-synced system time (both on the same machine, so clock skew is zero).

## 11. Is it possible to test this in local environment with Quix CLI? (Claude)

**Yes.** Two approaches:

1. **`quix run`** — The Quix CLI can start a local Kafka broker and run the source locally:
   ```bash
   cd ac_video_streaming
   quix run
   ```
   This starts a local Kafka, wires up topics, and runs `main.py`. Requires AC running on the same Windows machine.

2. **Local Kafka via Docker** — Start Kafka manually and point QuixStreams to it:
   ```bash
   # Start Kafka (e.g., via Docker on the same machine or another)
   docker run -d -p 9092:9092 apache/kafka
   # Run the video source without Quix Cloud
   cd ac_video_streaming
   python main.py
   ```
   Without `Quix__Sdk__Token` set, QuixStreams defaults to `localhost:9092`.

**Limitations for local testing:**
- `dxcam` requires a real Windows desktop session (no RDP, no headless)
- Assetto Corsa must be running with an active driving session
- For testing the pipeline without AC, you could mock the shared memory reader or use a test video file

## 12. End-to-end test results (2026-04-09)

### What was tested

Full pipeline from local Windows laptop to Quix Cloud and back to browser:

```
Laptop (mock AC + dxcam) → Kafka (Quix Cloud) → AC Video Viewer (Quix Cloud) → Browser
```

### Results

| Test | Result | Notes |
|---|---|---|
| Local mock capture + Kafka streaming | Working | Mock mode simulates AC session lifecycle, dxcam captures real screen |
| Quix Cloud viewer (WebSocket) | Working | Public URL serves live video stream from Kafka |
| Per-lap MP4 recording (local) | Working | 30s laps recorded at 1920x1080 @ 30fps, ~370KB per lap |
| Pause/resume during recording | Working | Mock pause triggers recording pause, resumes cleanly |
| Blob storage upload | Not tested | Requires `Quix__BlobStorage__Connection__Json` — not available locally, auto-injected only in Quix Cloud containers |

### Deployment architecture

- **`ac-video-viewer/`** — NEW, deployed to Quix Cloud. FastAPI + Kafka consumer + WebSocket. Public URL: `acvideoviewer-quixers-acquixbridge-videostreaming.az-france-0.app.quix.io`
- **`ac_video_streaming/`** — runs locally on Windows only (dxcam requirement). Connects to Quix Cloud Kafka via SDK token.
- The old `AC Video Streaming` cloud deployment was removed from `quix.yaml` (can't run in Linux containers).

### Configuration notes

- Portal API: `https://portal-api.cloud.quix.io` (NOT `platform.quix.io` — SSL handshake fails)
- Workspace: `quixers-acquixbridge-videostreaming`
- Python 3.12 required (`confluent-kafka` has no wheels for Python 3.14)
- FFmpeg required for MP4 recording (`winget install ffmpeg`)

### Next steps — blob storage upload

To enable blob upload from the local machine, the `Quix__BlobStorage__Connection__Json` env var is needed. This is auto-injected in Quix Cloud but must be manually configured for local use. Options:

1. **Ask Steve/DevOps** for the blob storage connection JSON from the workspace settings
2. **Run recording inside Quix Cloud** — not possible (dxcam requires Windows)
3. **Upload MP4s via a separate cloud service** — the local source could produce MP4 metadata to a Kafka topic, and a cloud-side service with blob storage access could fetch and store them

This relates to open question #2 (Steve).

## Open questions for the team

| # | Question | Owner | Status |
|---|---|---|---|
| 1 | Which SW to install on AC machine? | Claude | Answered |
| 2 | Storing in database in blob? | Steve | **Blocker** — need `Quix__BlobStorage__Connection__Json` for local upload, or alternative approach |
| 3 | Live stream or after-race file storage? | Claude/Tomas | **Both working locally** — decision pending from Tomas |
| 4 | MP4 per lap or per whole race? | Tomas/Onboarding | Currently per-lap. Pending decision. |
| 5 | How to start recording with race start? | Claude/Daniel | Answered |
| 6 | How to identify the correct display to record? | Claude | Answered |
| 7 | Store unfinished laps/races? On AC exit store MP4? | Claude | Answered — yes, always store |
| 8 | Pause recording when game is paused? | Claude | Answered — yes, follows `status` field |
| 9 | Disable recording or always on? | Claude | Answered — configurable via env var |
| 10 | Timestamp synchronization with lap data | Claude | Answered |
| 11 | Test in local environment with Quix CLI? | Claude | Answered — yes, via `quix run` or local Kafka |
| 12 | End-to-end cloud test | Ludvik/Claude | **Done** — live streaming works, blob upload blocked on #2 |
