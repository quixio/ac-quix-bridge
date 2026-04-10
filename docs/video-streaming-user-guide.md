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

Both are deployed in workspace `quixers-acquixbridge-videostreaming`.
