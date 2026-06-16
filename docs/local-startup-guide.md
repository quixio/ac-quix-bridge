# Local Startup Guide

How to run the AC telemetry and video capture services on the Windows machine where Assetto Corsa is running.

## Prerequisites

- Windows 10/11 with a real desktop session (no RDP — dxcam needs DirectX)
- Python 3.12 (`winget install Python.Python.3.12`)
- FFmpeg (`winget install ffmpeg`)
- Assetto Corsa installed and launchable
- `.env` files configured in both `ac-telemetry-source/` and `ac_video_streaming/` (see Configuration below)

## Quick start

```powershell
.\start-local.ps1
```

This single script handles everything:

1. Creates a shared `.venv` in the repo root (Python 3.12)
2. Installs dependencies from both services' `requirements.txt`
3. Adds ffmpeg to PATH (from WinGet install location)
4. Launches **AC Telemetry Source** in a new PowerShell window
5. Waits 7 seconds for telemetry to initialize and publish its session ID
6. Launches **AC Video Streaming** in a second PowerShell window

Each service runs in its own window. Press `Ctrl+C` in either window to stop it.

### Why the 7-second delay?

The video source subscribes to the `ac-telemetry-session` Kafka topic to adopt the telemetry's session ID. This ensures the MP4 filenames match the data lake paths so the Telemetry Explorer can find the video. The delay gives the telemetry source time to:

- Connect to Quix Cloud (portal API call, ~2s)
- Detect AC's shared memory
- Publish the first session message

Without the delay, the video source might generate its own session ID (logged as a warning). The recording still works, but won't be syncable in the Explorer.

## Manual start (without the script)

If you prefer to start each service separately:

### Terminal 1 — Telemetry Source

```powershell
cd ac-telemetry-source
..\.venv\Scripts\Activate.ps1
python main.py
```

### Terminal 2 — Video Streaming (start after telemetry is running)

```powershell
cd ac_video_streaming
..\.venv\Scripts\Activate.ps1
python main.py
```

## Configuration

### ac-telemetry-source/.env

```env
Quix__Sdk__Token=<your Quix SDK token or PAT>
Quix__Portal__Api=https://portal-api.dev.quix.io
Quix__Workspace__Id=<your workspace ID>
output=ac-telemetry-raw
session_output=ac-telemetry-session
```

### ac_video_streaming/.env

```env
Quix__Sdk__Token=<your Quix SDK token or PAT>
Quix__Portal__Api=https://portal-api.dev.quix.io
Quix__Workspace__Id=<your workspace ID>
AC_MOCK_MODE=false
VIDEO_STREAM_ENABLED=true
VIDEO_RECORDING_ENABLED=true
VIDEO_FPS=15
output=ac-video-frames
Quix__BlobStorage__Connection__Json=<S3 credentials JSON>
```

Key variables:

| Variable | Description |
|---|---|
| `AC_MOCK_MODE` | `true` to test without AC (captures your screen, simulates session events) |
| `VIDEO_FPS` | Capture frame rate (default 15). Higher = smoother video but larger files |
| `VIDEO_DISPLAY_INDEX` | Which monitor to capture (0 = primary) |
| `VIDEO_RECORDING_ENABLED` | `false` to disable MP4 recording (live stream only) |
| `SIDECAR_SAMPLE_HZ` | How often to sample normPos for the sync sidecar (default 5) |

## What happens when you start

1. **Telemetry source** connects to AC's shared memory and waits for the game to go LIVE
2. **Video source** initializes dxcam, subscribes to the telemetry session topic, and waits
3. When the driver **leaves the pit** and the car **crosses the start/finish line**, video recording begins
4. Each **lap completion** finalizes the current MP4, starts the next lap's recording, and uploads to S3 in the background
5. When the session **ends** (OFF status or game closes), the last recording is finalized and uploaded

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| "AC shared memory not available" | AC not running or not in a session | Start AC and enter a session |
| "dxcam not installed" | Missing dependency | `pip install dxcam` |
| "ffmpeg not found" | FFmpeg not in PATH | `winget install ffmpeg` or add to PATH |
| "No telemetry session_id received" | Telemetry source not running | Start telemetry first, or increase the delay in `start-local.ps1` |
| "Blob storage not available" | Missing or invalid S3 credentials | Check `Quix__BlobStorage__Connection__Json` in `.env` |
| Video not found in Explorer | Session ID mismatch between telemetry and video | Ensure both services use the same Quix workspace and the telemetry source is running |
