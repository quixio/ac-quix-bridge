# AC Video Streaming — User Guide

## Overview

The video streaming system captures Assetto Corsa gameplay from a Windows machine and makes it available in two ways:

1. **Live stream** — watch the race in real time from any browser
2. **Recorded sessions** — browse and download per-lap MP4 recordings from S3 storage

## Architecture

```
Windows laptop (AC machine)          Quix Cloud
+--------------------------+         +---------------------------+
| ac_video_streaming/      |         |                           |
|   dxcam (screen capture) | ------> | Kafka: ac-video-frames    |
|   ffmpeg (MP4 recording) |         |    |                      |
|   quixportal (S3 upload) | --+     |    v                      |
+--------------------------+   |     | AC Video Viewer (live)    |---> Browser
                               |     | AC Video Browser (files)  |---> Browser
                               |     +---------------------------+
                               |
                               +---> S3: quixdatalaketest/ac_video/
```

## Watching the live stream

### 1. Start the capture on the AC machine

Open a terminal on the Windows machine where Assetto Corsa is running:

```bash
cd ac_video_streaming
.venv\Scripts\activate
python main.py
```

The capture starts automatically when AC's status changes to LIVE (race start). It pauses when the game is paused and stops when the session ends.

### 2. Open the live viewer

Open this URL in any browser:

```
https://acvideoviewer-quixers-acquixbridge-videostreaming.az-france-0.app.quix.io
```

You will see:
- The live video feed from the AC machine
- Current session ID and lap number
- FPS counter and latency indicator
- Connection status (green = connected)

The stream runs at 15 FPS (configurable via `STREAM_FPS`) at 1280px width (configurable via `STREAM_WIDTH`).

### 3. Stop the stream

Press `Ctrl+C` in the terminal on the AC machine, or close AC. The capture detects the session end and stops cleanly.

## Testing without Assetto Corsa (mock mode)

You can test the full pipeline without AC by enabling mock mode:

```bash
cd ac_video_streaming
.venv\Scripts\activate
set AC_MOCK_MODE=true
python main.py
```

Mock mode captures your actual screen (whatever is on the display) and simulates AC session events: race start, lap changes, pause/resume, and session end.

## Browsing and downloading recordings

### 1. Open the video browser

Open this URL in any browser:

```
https://acvideobrowser-quixers-acquixbridge-videostreaming.az-france-0.app.quix.io
```

### 2. Select a session

The browser shows a list of all recorded sessions, sorted by date (newest first). Each session ID is a UTC timestamp indicating when the session started.

Click a session to see the per-lap MP4 files.

### 3. Download or preview files

For each lap file you can:
- **Play** — preview the video directly in the browser
- **Download** — save the MP4 file to your computer
- **Download all** — download all files from the session at once

### S3 storage structure

Recordings are stored in the S3 bucket `quixdatalaketest` with this structure:

```
ac_video/
  session_id=2026-04-10T08-14-25.001Z/
    2026-04-10T08-14-25.001Z_lap000.mp4    (lap 0)
    2026-04-10T08-14-25.001Z_lap001.mp4    (lap 1)
    2026-04-10T08-14-25.001Z_lap002.mp4    (lap 2)
```

Each MP4 is named with the session timestamp and lap number.

## Recording behavior

| AC Status | What happens |
|---|---|
| OFF -> LIVE | New session starts, recording begins (lap 0) |
| Lap change | Current lap MP4 finalized, uploaded to S3, next lap recording starts |
| LIVE -> PAUSE | Recording pauses (no frames captured, ffmpeg pipe stays open) |
| PAUSE -> LIVE | Recording resumes seamlessly |
| LIVE -> OFF | Recording finalized, uploaded to S3, streaming stops |
| AC closed / crash | Recording finalized and uploaded (incomplete session preserved) |

Incomplete sessions are always saved. No data is lost on unexpected exits.

## Configuration

All settings are in `ac_video_streaming/.env`:

| Variable | Default | Description |
|---|---|---|
| `Quix__Sdk__Token` | (required) | Quix Cloud SDK token or PAT |
| `Quix__Portal__Api` | `https://portal-api.cloud.quix.io` | Quix portal API URL |
| `Quix__Workspace__Id` | (required) | Quix workspace ID |
| `Quix__BlobStorage__Connection__Json` | (required for upload) | S3/Azure blob storage credentials JSON |
| `AC_MOCK_MODE` | `false` | Enable mock mode (no AC needed) |
| `VIDEO_DISPLAY_INDEX` | `0` | Which monitor to capture (0 = primary) |
| `VIDEO_FPS` | `30` | Capture and recording frame rate |
| `STREAM_FPS` | `15` | Live stream frame rate (lower = less bandwidth) |
| `STREAM_WIDTH` | `1280` | Max width for streamed frames |
| `JPEG_QUALITY` | `75` | JPEG compression for streamed frames (1-100) |
| `VIDEO_RECORDING_ENABLED` | `true` | Enable per-lap MP4 recording |
| `VIDEO_STREAM_ENABLED` | `true` | Enable live Kafka streaming |
| `VIDEO_OUTPUT_DIR` | `./recordings` | Local directory for MP4 files |
| `BLOB_VIDEO_PREFIX` | `ac_video` | S3 path prefix for uploads |

## Prerequisites (AC machine)

- Windows 10/11 with a real desktop session (no RDP)
- Python 3.12 (`winget install Python.Python.3.12`)
- FFmpeg (`winget install ffmpeg`)
- GPU with DirectX 11+ (any modern GPU)

## Quix Cloud deployments

| Deployment | Purpose | Public URL |
|---|---|---|
| AC Video Viewer | Live video stream viewer | `acvideoviewer-...az-france-0.app.quix.io` |
| AC Video Browser | Browse and download recorded sessions | `acvideobrowser-...az-france-0.app.quix.io` |
| Telemetry Explorer | Compare telemetry across sessions/laps with track map, corner overlays, synced marker | `telemetryexplorer-...az-france-0.app.quix.io` |

All deployed in workspace `quixers-acquixbridge-videostreaming`.

## Telemetry Explorer — analyzing lap data

The Telemetry Explorer lets you compare data across multiple sessions and laps with interactive plots, a live track map, and corner-severity overlays.

### Main features

- **Session/lap overlay** — pick multiple sessions and laps from any combination of filters (environment, rig, experiment, driver, track, car, session). Overlay their telemetry on the same plots.
- **Signal picker** — choose any combination of telemetry channels (speed, throttle, brake, RPM, tyre temps, brake temps, suspension, and many more) organized by category.
- **Collapsible control panels** — the session/lap selection and signal picker can be collapsed to save screen space once you've set them up.
- **Sticky track map panel** (top-right) — 2D map of the current track rendered from the CSV in `tracks/`. Corner segments are colored by severity (hairpin/tight/sweeper/straight). Corners are labeled T1..Tn. Legend at the top. Start/Finish marker at `normalizedDistance=0`.
- **Track zoom slider** — zoom the map from 1× (full track) to 8×. When zoomed above 1×, the view follows the red dot automatically. Mouse zoom/pan on the map is disabled — the slider is the only way to change zoom.
- **Red position dot** — on the track map, indicates the current marker position. Updates instantly as you drag the plot marker.
- **Synced draggable marker** — a red vertical line on every plot, draggable by mouse. Dragging it on any plot updates all other plots + the track dot simultaneously. Position persists across lap changes and re-plots.
- **Per-trace value annotations** — up to 6 values per plot are shown **stacked in a column** next to the vertical marker, pinned to the top of the plot. Each label is boxed in the trace's color and never overlaps regardless of where values fall. If more than 6 traces, a "+N" badge appears at the bottom of the stack.
- **Position readout** — under the track map, shows the current `normalizedCarPosition` as both percentage and meters.
- **Video placeholder** — reserved area in the track panel where video playback will appear once wall-clock sync is implemented.
- **Per-plot corner overlay** — each plot has a "Show corners" checkbox next to the title. When enabled, the plot is shaded with corner regions matching the track map colors + T1..Tn labels.

### How it works under the hood

- Track data: `telemetry-comparison/tracks/ks_nurburgring/layout_sprint_a.csv` — one row per track point with x, y, z, distance, corner radius, ideal speed, width
- Corner classification: `telemetry-comparison/tracks_config.json` — thresholds (hairpin <60m, tight 60–150m, sweeper 150–400m, straight ≥400m) and colors are editable
- Join key between telemetry and track: `normalizedCarPosition` (telemetry) ↔ `normalizedDistance` (track CSV), both in range 0.0–1.0
- Data source: DuckDB queries against QuixLake (Parquet + Iceberg), with partitioning by environment/rig/experiment/driver/track/carModel/session_id/lap

### Adding a new track

1. Drop the track CSV into `telemetry-comparison/tracks/<track_name>/layout_xxx.csv`
2. Update `default_track` in `tracks_config.json` (multi-track support will auto-detect based on the session's `track` field in future iterations)
3. Redeploy the Telemetry Explorer
