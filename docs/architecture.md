# Architecture — Data Capture and Recording

How the services that capture raw telemetry and video from Assetto Corsa connect to each other and to the cloud.

## System overview

```
  Windows PC (AC machine)                          Quix Cloud
 +----------------------------------+             +----------------------------------+
 |                                  |             |                                  |
 |  Assetto Corsa                   |             |  Kafka Topics                    |
 |    |                             |             |    ac-telemetry-raw              |
 |    | shared memory (mmap)        |             |    ac-telemetry-session          |
 |    v                             |             |    ac-video-frames               |
 |  +----------------------------+  |             |                                  |
 |  | ac-telemetry-source        |  |  Kafka      |  +----------------------------+  |
 |  |  reads physics/graphics/   |--+------------>|  | ac-telemetry-lake          |  |
 |  |  static structs @ 60 Hz   |  |             |  |  writes Parquet to S3      |  |
 |  |  publishes to Kafka        |  |             |  +----------------------------+  |
 |  +----------------------------+  |             |                                  |
 |    |                             |             |  +----------------------------+  |
 |    | session_id via             |             |  | telemetry-dashboard        |  |
 |    | ac-telemetry-session       |             |  |  live telemetry charts     |  |
 |    v                             |             |  +----------------------------+  |
 |  +----------------------------+  |             |                                  |
 |  | ac_video_streaming         |  |  Kafka      |  +----------------------------+  |
 |  |  dxcam screen capture      |--+------------>|  | ac-video-viewer           |  |
 |  |  ffmpeg MP4 recording      |  |             |  |  live video stream        |  |
 |  |  per-lap sidecar JSON      |  |             |  +----------------------------+  |
 |  +-------------+--------------+  |             |                                  |
 |                |                 |             |  +----------------------------+  |
 |                | S3 upload       |             |  | ac-video-browser          |  |
 |                | (per lap)       |             |  |  browse/download MP4s     |  |
 +----------------+-----------------+             |  +----------------------------+  |
                  |                               |                                  |
                  v                               |  +----------------------------+  |
          +---------------+                       |  | telemetry-comparison       |  |
          |  S3 Bucket    |<----------------------|  |  (Telemetry Explorer)      |  |
          |               |   reads Parquet +     |  |  lap overlay, track map,   |  |
          | ac_telemetry/ |   reads MP4/sidecar   |  |  video sync, annotations  |  |
          | ac_video/     |                       |  +----------------------------+  |
          +---------------+                       +----------------------------------+
```

## Data capture services (Windows)

### ac-telemetry-source

**Purpose:** Read AC's shared memory and publish telemetry to Kafka.

- **Input:** Windows shared memory (`acpmf_physics`, `acpmf_graphics`, `acpmf_static`) via `ctypes` + `mmap`
- **Output:** Two Kafka topics:
  - `ac-telemetry-raw` — high-frequency telemetry (speed, throttle, brake, tyres, suspension, etc.) at configurable Hz (default 60)
  - `ac-telemetry-session` — session metadata on each session change (car, track, session ID). Compacted topic with infinite retention.
- **Session detection:** New session = `car|track` key change. Session ID is a UTC timestamp string.
- **Runs as:** QuixStreams `Source` (custom subclass). Deployed locally on the AC machine.

### ac_video_streaming

**Purpose:** Capture the game screen, record per-lap MP4s, and stream live frames.

- **Input:** 
  - Screen pixels via dxcam (DirectX Desktop Duplication)
  - AC shared memory (graphics/static) for status, lap, and position detection
  - `ac-telemetry-session` Kafka topic — adopts the telemetry session ID so MP4 paths match the data lake
- **Output:**
  - Per-lap MP4 files + sidecar JSON uploaded to S3 (`ac_video/session_id=.../`)
  - JPEG frames to Kafka topic `ac-video-frames` for live streaming
- **Key behaviors:**
  - **Start-line detection:** Recording begins only when `normalizedCarPosition` crosses from >0.9 to <0.05 (the start/finish line). The out-lap and pitstop are not recorded.
  - **Non-blocking session ID:** Recording starts immediately with a temporary ID. The telemetry session ID is adopted asynchronously; the MP4 is renamed at finalization.
  - **Background upload:** S3 upload runs in a background thread so the capture loop isn't blocked between laps.
  - **Keyframe interval:** ffmpeg encodes with one keyframe per second (`-g {fps}`) for fast seeking in the Explorer.
  - **Sidecar JSON:** Each MP4 gets a `.sync.json` with sub-sampled frame data (wall-clock, normalizedCarPosition) at 5 Hz for video-telemetry sync.
- **Runs as:** QuixStreams `Source`. Deployed locally on the AC machine.

## How the two capture services coordinate

```
ac-telemetry-source                      ac_video_streaming
      |                                        |
      |--- publishes session_id to ----------->|  (Kafka: ac-telemetry-session)
      |    ac-telemetry-session                |
      |                                        |--- subscribes via SessionTracker
      |                                        |    (background thread, auto_offset_reset=earliest)
      |                                        |
      |                                        |--- on new session: polls tracker
      |                                        |    for the telemetry session_id
      |                                        |
      |                                        |--- adopts it (or falls back to local id)
      |                                        |
      |--- publishes telemetry to              |--- records MP4 with adopted session_id
      |    ac-telemetry-raw                    |--- uploads to S3: ac_video/session_id=<same id>/
```

Both services read AC's shared memory independently. They coordinate only through the Kafka session topic. The `start-local.ps1` script launches telemetry 7 seconds before video to ensure the session message is available when the video source starts.

## Cloud services

### ac-telemetry-lake

Consumes `ac-telemetry-raw` and writes Hive-partitioned Parquet files to S3. Partitioned by: `environment / test_rig / experiment / driver / track / carModel / session_id / year / month / day / hour / lap`. Optional Iceberg catalog registration.

### telemetry-comparison (Telemetry Explorer)

FastAPI web app for cross-session/lap analysis. Queries the Parquet data lake via DuckDB (through QuixLake). Serves:
- Lap overlay plots (Plotly.js)
- Track map with corner classification
- Synced draggable marker across all plots
- Video panel with bidirectional sync (blob-buffered MP4 + sidecar lookup)

Handles session ID format differences between Quix Cloud and Dev environments when looking up video files.

### ac-video-viewer

WebSocket-based live video viewer. Consumes JPEG frames from `ac-video-frames` Kafka topic and pushes to browser clients.

### ac-video-browser

File browser for S3-stored MP4 recordings. Lists sessions, shows per-lap files, allows preview and download.

### telemetry-dashboard

Live telemetry charts. Background Kafka consumer feeds WebSocket to browser clients showing real-time signals.

## Storage layout (S3)

```
s3://quixdatalaketest/
  ac_telemetry/                           # Parquet data lake
    environment=.../test_rig=.../...
      session_id=.../lap=.../
        *.parquet

  ac_video/                               # Video recordings
    session_id=2026-04-14T11-42-08.107Z/
      2026-04-14T11-42-08.107Z_lap002.mp4
      2026-04-14T11-42-08.107Z_lap002.sync.json
      2026-04-14T11-42-08.107Z_lap003.mp4
      2026-04-14T11-42-08.107Z_lap003.sync.json
```

Lap numbering: `lap = completedLaps + 1`. Lap 1 is the out-lap (typically not recorded by the video source due to start-line detection). Lap 2 is the first timed lap.

## Deployment topology

| Service | Runs on | Why |
|---|---|---|
| ac-telemetry-source | Windows PC (local) | Requires AC shared memory (Windows mmap) |
| ac_video_streaming | Windows PC (local) | Requires dxcam (DirectX, real display) |
| ac-telemetry-lake | Quix Cloud (Linux) | Kafka consumer, S3 writer |
| telemetry-dashboard | Quix Cloud (Linux) | Web service, Kafka consumer |
| ac-video-viewer | Quix Cloud (Linux) | Web service, Kafka consumer |
| ac-video-browser | Quix Cloud (Linux) | Web service, S3 reader |
| telemetry-comparison | Quix Cloud (Linux) | Web service, DuckDB + S3 reader |
