# Video ↔ Telemetry Sync — Design & Implementation Plan

> **Status: IMPLEMENTED 2026-04-13.** Validated end-to-end on the AC machine.
> See the "What actually got built" section at the bottom for deviations from
> this original design.

Design doc for wiring MP4 lap recordings to the Telemetry Explorer so analysts can scrub plot markers and see the matching video frame (and vice versa).

## The problem

MP4 files currently have:
- Relative timestamps only (assumes constant 30 FPS: frame k → k/30 seconds)
- Session start time encoded in the filename (`2026-04-13T09-30-11.402Z_lap001.mp4`)

They do **not** have:
- Per-frame wall-clock timestamps
- Any mapping to `normalizedCarPosition`

This breaks sync because the MP4 doesn't track pause gaps or dropped frames.

## Solution: sidecar JSON per MP4

Each MP4 gets a twin JSON file uploaded to the same S3 folder:

```
ac_video/session_id=2026-04-13T09-30-11.402Z/
  2026-04-13T09-30-11.402Z_lap001.mp4
  2026-04-13T09-30-11.402Z_lap001.sync.json   <-- NEW
```

### Sidecar format

```json
{
  "session_id": "2026-04-13T09:30:11.402Z",
  "lap": 1,
  "start_wall_ms": 1776162611402,
  "fps": 30,
  "duration_ms": 94533,
  "frame_count": 2836,
  "frames": [
    { "idx": 0,    "t_ms": 0,     "wall_ms": 1776162611402, "normPos": 0.0000 },
    { "idx": 30,   "t_ms": 1000,  "wall_ms": 1776162612402, "normPos": 0.0213 },
    { "idx": 60,   "t_ms": 2000,  "wall_ms": 1776162613402, "normPos": 0.0427 }
  ]
}
```

- **`t_ms`** — video playback time (monotonically increasing; excludes pause gaps)
- **`wall_ms`** — wall-clock time to correlate with telemetry `timestamp_ms`
- **`normPos`** — normalizedCarPosition at that frame (if available from shared memory at capture time)
- **Sampling**: store every Nth frame (e.g. every 30th = 1 Hz) plus any frame right before/after a pause. Interpolation between samples is fine.

### Why also embed `normPos`?

Telemetry Explorer's join key is `normalizedCarPosition`. If the sidecar carries this per video-time, scrubbing the video → looking up normPos → moving the marker on plots is a single table lookup, no telemetry join needed.

Marker-to-video works the same way in reverse: marker at normPos → binary-search sidecar for matching frame → `video.currentTime = t_ms / 1000`.

## Implementation

### 1. Capture side (on the AC machine)

**File: `ac_video_streaming/video_recorder.py`**
- Add a new method `log_frame(wall_ms, norm_pos)` called for every frame written to ffmpeg
- Track the MP4 frame count internally
- On `finish_lap()`: dump the recorded frame log to a sidecar JSON next to the MP4

**File: `ac_video_streaming/video_source.py`**
- In the main capture loop, after `recorder.write_frame(frame)`, call `recorder.log_frame(wall_ms, norm_pos)` with values from the current AC graphics read
- On pause/resume, don't log anything (frames aren't written, so no entries)
- The `_upload_to_blob()` method uploads both the MP4 and the `.sync.json`, then deletes both locally

### 2. Storage side (S3)

No new structure — just uploads the JSON to the same folder as the MP4:
```
ac_video/session_id=.../<id>_lap<NNN>.mp4
ac_video/session_id=.../<id>_lap<NNN>.sync.json
```

### 3. Consumer side (Telemetry Explorer)

**File: `telemetry-comparison/main.py`**
- New endpoint `GET /api/video/{session_id}/{lap}` — returns:
  ```json
  {
    "mp4_url": "https://<s3>/ac_video/session_id=.../file.mp4",
    "sync": { ...sidecar JSON contents... }
  }
  ```
- The S3 URL can be a signed short-lived URL, or the endpoint streams the MP4 itself (proxy via FastAPI)
- For the first cut, proxy-streaming is simpler and avoids CORS headaches

**File: `telemetry-comparison/static/index.html`**
- Replace the `#video-placeholder` with a real `<video>` element when a session is selected
- Fetch the sidecar JSON for the **first checked lap** (or a dropdown if multiple)
- Build two lookup tables client-side:
  - `nd → t_ms` for marker→video sync
  - `t_ms → nd` for video→marker sync
- Event wiring:
  - On marker drag (`updateMarker`): `video.currentTime = lookupTms(normPos) / 1000`, but only if `|video.currentTime - new| > 0.05s` to avoid feedback loops
  - On `video.timeupdate`: `updateMarker(lookupNd(video.currentTime * 1000))`
- Default selection: first lap of first selected session has its video loaded

### 4. Multiple laps overlaid

When the user selects multiple laps, only ONE video plays at a time (they can't be in sync with multiple laps anyway). Add a small dropdown inside the video panel: "Playing: Lap 2 (driver X, session Y)". The marker still drives sync only for the laps corresponding to that video's session/lap, but plot annotations show values for all selected traces.

## Known edge cases

- **Pause in game** — ffmpeg pipe is paused, no frames written, no sidecar entries. Video playback time stays continuous (it doesn't know about the pause). Wall clock has a gap.
- **Lap split mid-frame** — the recorder finishes one MP4 and starts another. Two sidecars. No loss of sync.
- **Sample rate trade-off** — storing 1 entry/second keeps sidecars tiny (~100 entries per lap ≈ 6 KB). Interpolation inside the analyst's browser handles sub-second accuracy.
- **Clock drift** — the capture PC and any telemetry source share `time.time()`, so there is no drift (same process, same machine).
- **Backward compatibility** — MP4s already in S3 don't have sidecars. The video panel in Telemetry Explorer should gracefully show "Video sync not available for this session" when the sidecar 404s.

## Sanity tests

1. Record a 30s lap in mock mode, verify sidecar JSON is written next to MP4 locally before upload
2. Verify S3 has both files after upload
3. Load the Telemetry Explorer, select a recent session/lap with sidecar, confirm video loads
4. Drag plot marker → confirm video seeks to matching position
5. Play video → confirm plot marker moves smoothly
6. Test pause: record a lap with a mid-lap pause, verify marker position still matches game state after resume

## File inventory (what gets touched)

Capture:
- `ac_video_streaming/video_recorder.py` (+ ~40 lines: log_frame, sidecar writer)
- `ac_video_streaming/video_source.py` (+ ~10 lines: call log_frame, upload JSON)

Cloud:
- `telemetry-comparison/main.py` (+ ~30 lines: video endpoint)
- `telemetry-comparison/static/index.html` (+ ~100 lines: video element, sidecar fetch, sync wiring)

---

## What actually got built (deviations from this doc)

The core approach (sidecar JSON per MP4, proxy-streamed MP4s, bidirectional
join via `normalizedCarPosition`) all landed as described. A few things changed
once we started implementing:

### 1. Session-ID alignment — added (not in original doc)

The original design assumed MP4 filenames and telemetry rows shared a
`session_id`. In practice they did not: `ac-telemetry-source` and
`ac_video_streaming` each generated their own `session_id` on off→live, so
they differed by tens of milliseconds to tens of seconds (telemetry typically
detected the transition first, while video waited for the driver to leave the
pit lane). That broke the Explorer's folder lookup.

Fix: video source subscribes to the `ac-telemetry-session` Kafka topic
(compacted, infinite retention) and **adopts** the telemetry-published
session_id on each new-session detection. If telemetry is unreachable, video
falls back to a locally-generated id (logs a warning; the recording won't be
syncable in the Explorer, but the MP4 is still saved).

Implementation: `ac_video_streaming/session_tracker.py` (thread-safe holder)
plus a background thread in `video_source._start_session_tracker_thread`.
The thread runs inside the Source's subprocess (QuixStreams spawns Sources
via `multiprocessing`) and uses its own `Application.get_consumer()` — the
SessionTracker holds a `threading.Lock` and cannot be pickled across the
process boundary.

Important detail: Quix Cloud prefixes topic names with the workspace id. The
tracker resolves the prefixed name via `mini_app.topic(name).name` before
calling `consumer.subscribe(...)`. Subscribing to the bare name silently
matches nothing.

### 2. Play / pause mode switch instead of bidirectional tolerance

The original doc proposed bidirectional sync guarded by a 0.05s tolerance
check to avoid feedback loops. The shipped UI uses a cleaner mode switch:

- Video **playing** → `timeupdate` drives the marker + red dot (user cannot
  fight the video; dragging during playback pauses + seeks).
- Video **paused** → dragging the marker seeks the video.

Implementation in `static/index.html`: `updateMarker(nd, forceTrack, source)`
takes a `source` tag so internal callers (`'video'`, `'drag'`, undefined)
can choose the right policy. `syncVideoFromMarker` only acts when
`source === 'drag'`.

### 3. Sidecar sampling at 5 Hz + forced boundary samples

The doc suggested 1 Hz (every 30th frame). We went with 5 Hz (every 6th
frame; configurable via `SIDECAR_SAMPLE_HZ`) for smoother scrubbing, plus
forced samples at pause boundaries and end-of-lap so wall-clock gaps and
lap tails are anchored. Size remains tiny (~30–60 KB for a typical lap).

### 4. Telemetry Explorer: blob binding + whole-file proxy

- Added `blobStorage: bind: true` + `BLOB_VIDEO_PREFIX` to the Telemetry
  Explorer deployment in `quix.yaml` (same pattern as Video Browser).
- Added `quixportal[all]==2.0.1` + the Azure DevOps extra-index-url to
  `telemetry-comparison/requirements.txt`.
- `GET /api/video/{session_id}/{lap}` returns `{has_video, has_sync, sync,
  mp4_url, message}`.
- `GET /api/video/{session_id}/{lap}/mp4` proxy-streams the MP4 as a single
  whole-file response (no HTTP Range). For typical lap sizes (hundreds of
  KB to a few MB), the browser buffers once and then in-memory seeking is
  free.

### 5. Multi-lap — dropdown inside the video panel

When the user checks multiple laps, the video panel shows a `<select>` of
all currently-plotted laps. The first lap auto-loads; the user can switch
to any other selected lap. Only one video plays at a time (which is the
only sensible behavior).

### 6. startup ordering

`start-local.ps1` launches telemetry first, then waits 7 seconds before
launching video. This removes a timing race in the worst case (AC already
LIVE at launch, video racing ahead of telemetry's first publish). Without
it, adoption still usually works thanks to topic compaction + the tracker's
`auto_offset_reset=earliest`, but the delay makes startup bulletproof.

### 7. Mock-mode normPos is now monotonic per lap

`ac_reader_mock.py` was emitting `(current_time % 60000) / 60000` (a
meaningless 60-second ramp). Replaced with a per-lap 0→1 ramp so end-to-end
sync can be exercised without AC.

### 8. Lap numbering — `+1` convention

The data lake sink (`ac-telemetry-lake/main.py`) does
`lap = completedLaps + 1` (out-lap = "lap 1 in progress"). The video source
originally used raw `completedLaps` for MP4 filenames, so the Explorer was
asking for `_lapNNN.mp4` where N was off-by-one from the actual file. Fix:
video source applies the same `+1` so MP4/sidecar filenames align with
telemetry lap numbers in the Explorer dropdown.
