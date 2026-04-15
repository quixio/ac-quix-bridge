---
name: video-capture
description: How per-lap MP4 video capture works in ac_video_streaming. Use when modifying recording behavior, lap detection, session ID adoption, start-line detection, ffmpeg encoding, sidecar JSON generation, or S3 upload logic.
user-invocable: false
---

## Video Capture Pipeline

The video capture system lives in `ac_video_streaming/` and runs locally on the Windows AC machine. It records per-lap MP4 files with sidecar JSON metadata and uploads both to S3.

### Key files

| File | Purpose |
|---|---|
| `video_source.py` | Main capture loop, state machine, session/lap detection, start-line crossing |
| `video_recorder.py` | ffmpeg subprocess management, frame writing, sidecar generation, remux, rename |
| `session_tracker.py` | Thread-safe holder for the telemetry session ID (consumed from Kafka) |
| `ac_reader.py` | Reads AC shared memory (graphics + static structs) |
| `ac_reader_mock.py` | Mock reader for testing without AC |
| `main.py` | Entry point, creates QuixStreams Application + ACVideoSource |

### State machine (video_source.py ACVideoSource.run)

The capture loop reads AC shared memory each iteration and follows this state machine:

```
off/None --> LIVE (out of pit):  new session detected
                                 - resolve session_id (non-blocking)
                                 - set waiting_for_start_line = True
                                 - do NOT start recording yet

waiting_for_start_line:          each frame, check normalizedCarPosition
                                 - if normPos wraps from >0.9 to <0.05: START RECORDING
                                 - if normPos already <0.05 (two consecutive reads): START
                                 - if first read and normPos <0.05: START

LIVE (recording):                capture frame, write to ffmpeg, log sidecar metadata
                                 - completedLaps increases: finish_lap, start_lap (new ffmpeg)
                                 - S3 upload runs in background thread

LIVE --> pause:                  recorder.pause() (ffmpeg pipe stays open, frames skipped)
pause --> LIVE:                  recorder.resume()
LIVE --> off:                    finish_lap, upload, cleanup
in_pit:                          recorder.pause() while in pit lane
```

### Session ID adoption (non-blocking)

Recording must NOT block waiting for the telemetry session ID. The flow is:

1. On new session: try `session_tracker.try_get_fresh_session_id()` (non-blocking lock read)
2. If available: use it immediately
3. If not: start with `_fallback_session_id()` (UTC timestamp), set `session_id_confirmed = False`
4. Each frame: poll the tracker. When the real ID arrives, call `recorder.update_session_id()`
5. At `finish_lap()`: `_rename_to_session_id()` renames the MP4 from temp name to real name
6. After 15s timeout: accept whatever the tracker has (cold-start case) or log warning

The `SessionTracker` runs in a background thread consuming `ac-telemetry-session` via a mini QuixStreams Application. Topic names must be workspace-prefixed (resolved via `mini_app.topic(name).name`).

### VideoRecorder lifecycle

```python
recorder.start_lap(session_id, lap, width, height)
  # Opens ffmpeg subprocess, resets sidecar state
  # Filename: {safe_session_id}_lap{lap:03d}.mp4

recorder.write_frame(frame)
  # Resizes if needed, writes raw RGB bytes to ffmpeg stdin
  # Increments frame_index

recorder.log_frame(wall_ms, norm_pos)
  # Records sidecar sample every N frames (N = fps / sidecar_sample_hz)
  # Forced samples on pause boundaries and end-of-lap

recorder.pause() / recorder.resume()
  # Sets _paused flag. write_frame/log_frame are no-ops while paused
  # Forced sample on pause, force_next_sample on resume

recorder.update_session_id(new_id)
  # Updates internal _session_id. File rename happens in finish_lap()

recorder.finish_lap() -> str
  # 1. Anchor last frame in sidecar
  # 2. Close ffmpeg stdin, wait for process
  # 3. Compute actual fps from wall-clock span
  # 4. Remux if actual fps differs from declared (ffmpeg -c copy)
  # 5. Rename MP4 if session_id was updated (_rename_to_session_id)
  # 6. Write sidecar JSON (<mp4>.sync.json)
  # Returns the final MP4 path
```

### ffmpeg encoding

```
ffmpeg -y -f rawvideo -pix_fmt rgb24 -s WxH -r FPS -i -
       -c:v libx264 -preset fast -crf 28 -g FPS -pix_fmt yuv420p output.mp4
```

- `-g FPS` = one keyframe per second for fast seeking in the browser
- `-crf 28` = moderate quality, small files (~5-20 MB per 90s lap at 15fps 1080p)
- `-preset fast` = balance encoding speed vs compression

### Sidecar JSON format

```json
{
  "session_id": "2026-04-14T11:42:08.107Z",
  "lap": 2,
  "start_wall_ms": 1776162611402,
  "fps": 14.87,
  "duration_ms": 94533,
  "frame_count": 1407,
  "frames": [
    {"idx": 0, "t_ms": 0, "wall_ms": 1776162611402, "normPos": 0.0012},
    {"idx": 6, "t_ms": 403, "wall_ms": 1776162611805, "normPos": 0.0089},
    ...
  ]
}
```

- `fps` = effective fps after remux (may differ from declared due to capture jitter)
- `t_ms` = video playback time (matches MP4 timeline after remux)
- `wall_ms` = wall-clock for correlation with telemetry `timestamp_ms`
- `normPos` = AC's `normalizedCarPosition` at capture time
- Sampled at `SIDECAR_SAMPLE_HZ` (default 5) + forced samples at pause/resume/end-of-lap

### normalizedCarPosition re-read

The sidecar's `normPos` is re-read from shared memory right after the frame is grabbed (not from the stale read at the top of the loop). A guard prevents finish-line crossings from contaminating the current lap's sidecar:

```python
if old_norm > 0.8 and norm_pos < 0.2:
    norm_pos = old_norm  # keep pre-crossing value
```

### S3 upload

Files are uploaded to `{BLOB_VIDEO_PREFIX}/session_id={safe_session_id}/`. The session_id has colons replaced with hyphens for path safety. Both MP4 and sidecar JSON are uploaded; local files are deleted after successful upload. Upload runs in a background thread during lap changes.
