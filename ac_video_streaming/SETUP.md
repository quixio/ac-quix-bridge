# AC Video Streaming — Setup & Prerequisites

## Hardware Requirements

- **Windows 10/11** — dxcam uses the DirectX Desktop Duplication API (Windows-only)
- **GPU with DirectX 11+** — any modern dedicated or integrated GPU
- **Real desktop session** — screen capture does NOT work over RDP or headless sessions

## Software Prerequisites

### 1. Python 3.12+

Download from https://www.python.org/downloads/ or install via winget:
```bash
winget install Python.Python.3.12
```

### 2. FFmpeg

Required for MP4 recording. Install via winget:
```bash
winget install ffmpeg
```
Or download from https://ffmpeg.org/download.html and add to PATH.

Verify:
```bash
ffmpeg -version
```

### 3. Git (for Quix Cloud sync)

```bash
winget install Git.Git
```

### 4. Quix CLI (optional, for local Kafka)

```bash
pip install quixcli
```

## Python Environment Setup

```bash
cd ac_video_streaming
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Environment Variables

Copy from the project root `.env.example` or create `.env` in `ac_video_streaming/`:

```env
# Required for Quix Cloud Kafka connection (omit for local Kafka on localhost:9092)
Quix__Sdk__Token=<your Quix Cloud SDK token>

# Required for blob storage upload (auto-injected in Quix Cloud)
# Quix__BlobStorage__Connection__Json=<auto-injected>

# Video settings (all optional, defaults shown)
output=ac-video-frames
VIDEO_DISPLAY_INDEX=0
VIDEO_FPS=30
STREAM_FPS=15
VIDEO_OUTPUT_DIR=./recordings
VIDEO_RECORDING_ENABLED=true
VIDEO_STREAM_ENABLED=true
STREAM_WIDTH=1280
JPEG_QUALITY=75
BLOB_VIDEO_PREFIX=ac_video
```

## Running

### With Assetto Corsa

1. Launch Assetto Corsa and enter a driving session
2. Run the video source:
   ```bash
   cd ac_video_streaming
   .venv\Scripts\activate
   python main.py
   ```
3. The source will:
   - Connect to AC shared memory
   - Start capturing when AC status is LIVE
   - Save per-lap MP4s to `./recordings/`
   - Stream frames to Kafka topic `ac-video-frames`

### With Quix CLI (local Kafka)

```bash
cd ac_video_streaming
quix run
```

This starts a local Kafka broker automatically.

---

## Testing Without Assetto Corsa

You can test the video pipeline locally without AC installed by using the mock reader included in this project.

### Quick Test (mock mode)

Set the environment variable to enable mock mode:

```bash
set AC_MOCK_MODE=true
python main.py
```

In mock mode the source:
- Skips AC shared memory entirely
- Generates synthetic status transitions (off → live → lap changes → pause → live → off)
- Captures your actual screen via dxcam (whatever is on the display)
- Records MP4 files and streams to Kafka normally

This validates the full pipeline: screen capture → ffmpeg recording → per-lap MP4 → Kafka streaming → blob upload.

### What mock mode tests

| Component | Tested? | Notes |
|---|---|---|
| dxcam screen capture | Yes | Captures whatever is on the display |
| FFmpeg MP4 recording | Yes | Real MP4 files written to `./recordings/` |
| Per-lap file splitting | Yes | Mock generates lap transitions every ~20 seconds |
| Pause/resume | Yes | Mock pauses for 5 seconds mid-session |
| Kafka frame streaming | Yes | Requires Kafka (local or Quix Cloud) |
| Blob storage upload | Yes | Requires `Quix__BlobStorage__Connection__Json` |
| AC shared memory | No | Mocked — no AC needed |
| Timestamp sync with telemetry | Partial | Timestamps are real, but no telemetry to correlate with |

### What you still need for mock mode

- **Windows with a real desktop session** (dxcam requirement)
- **FFmpeg installed** (for MP4 recording)
- **Kafka** (for streaming — use `quix run` or local Docker Kafka, or set `VIDEO_STREAM_ENABLED=false` to skip)

### Testing without dxcam (fully headless)

If you don't have a Windows desktop (e.g., CI/CD), set both capture features off:
```bash
set VIDEO_RECORDING_ENABLED=false
set VIDEO_STREAM_ENABLED=false
python main.py
```
This runs only the AC shared memory state machine (or mock state machine) without any screen capture. Useful for testing the session detection logic.
