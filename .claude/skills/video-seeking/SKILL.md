---
name: video-seeking
description: How video seeking and bidirectional sync works in the Telemetry Explorer web UI. Use when modifying video playback, marker-video sync, sidecar lookup, blob buffering, or the video panel in telemetry-comparison/static/index.html.
user-invocable: false
---

## Video Seeking in the Telemetry Explorer

The Telemetry Explorer (`telemetry-comparison/`) displays a `<video>` element synced to telemetry plots via normalizedCarPosition. The sync is bidirectional: video drives the marker during playback, marker drives the video when paused.

### Key files

| File | Purpose |
|---|---|
| `telemetry-comparison/static/index.html` | Frontend: video element, sync logic, blob buffering, lookup tables |
| `telemetry-comparison/main.py` | Backend: video metadata endpoint, MP4 proxy, sidecar fetch, session ID format handling |

### Backend endpoints

#### GET /api/video/{session_id}/{lap}

Returns metadata + sidecar sync data:
```json
{
  "has_video": true,
  "has_sync": true,
  "sync": { "session_id": "...", "lap": 2, "fps": 14.87, "frames": [...] },
  "mp4_url": "/api/video/{session_id}/{lap}/mp4",
  "message": null
}
```

**Session ID format handling:** `_session_blob_variants(session_id)` generates possible blob-path forms to handle format differences between Quix Cloud (`2026-04-14T11:42:08.107Z`) and Dev (`2026-04-14 11:42:08.1070000`). The lookup tries each variant against S3.

**First-lap trim:** For lap 1, if `min(normalizedCarPosition) > 0.1` and no start-line wrap is detected, the telemetry endpoint returns empty data (pure out-lap, no useful full-circuit data).

#### GET /api/video/{session_id}/{lap}/mp4

Proxy-streams the MP4 from blob storage. Supports HTTP Range requests for seeking. Also tries session ID format variants.

### Frontend architecture

#### videoState (global)

```javascript
{
  element: HTMLVideoElement,  // the <video> DOM element
  frames: [...],              // sidecar frames sorted by t_ms (for video->marker)
  framesByNd: [...],          // sidecar frames sorted by normPos (for marker->video)
  blobUrl: string|null,       // object URL for fully-buffered MP4
  isPlaying: boolean,
  currentLoadToken: number,   // monotonic counter to discard stale async loads
  laps: [...],                // currently selectable laps
  currentLapIdx: number,
}
```

#### Video loading flow (loadVideoForLapIdx)

1. Fetch `/api/video/{session_id}/{lap}` for metadata + sidecar
2. Call `buildSyncLookups(meta.sync)` to create lookup tables
3. Send HEAD request to get Content-Length
4. If size <= 100 MB: fetch full MP4 as blob, create `URL.createObjectURL()`, set as `video.src`
5. If size > 100 MB: set `video.src` directly to the streaming URL (HTTP Range seeking)
6. Release previous blob URL via `URL.revokeObjectURL()` on each lap switch

Stale load protection: a monotonic `currentLoadToken` is checked after each `await` to discard results from superseded loads.

#### Sync lookup tables (buildSyncLookups)

Built from the sidecar's `frames` array. Each frame has `{idx, t_ms, wall_ms, normPos}`.

Two sorted arrays are maintained:
- `videoState.frames` — sorted by `t_ms` (used for video time -> normPos lookup)
- `videoState.framesByNd` — sorted by `normPos` (used for normPos -> video time lookup)

Both are searched with binary search + linear interpolation via `_interp()`.

#### Bidirectional sync — mode-switched, no feedback loops

The sync uses a **mode switch** pattern, not a tolerance hack:

**Video PLAYING** (video drives marker):
- A `requestAnimationFrame` loop (`_videoRafLoop`) reads `video.currentTime`
- Calls `lookupNormPosForTms(currentTime * 1000)` to find the track position
- Calls `updateMarker(normPos, true, 'video')` to move the marker + red dot
- The `source='video'` tag prevents `syncVideoFromMarker` from echoing back

**Video PAUSED** (marker drives video):
- User drags marker on any plot -> `updateMarker(nd, forceTrack, 'drag')`
- `syncVideoFromMarker(nd, 'drag')` fires:
  1. If video is playing, pause it first
  2. Call `lookupTmsForNormPos(nd)` to find the video time
  3. Set `video.currentTime = t_ms / 1000` (only if delta > 15ms to avoid churn)

**seeked event** (reflects native video control seeks):
- When paused, a `seeked` listener looks up normPos from the new currentTime
- Calls `updateMarker(normPos, true, 'video')` to sync the marker

#### Lookup functions

```javascript
// marker position -> video time (for seeking)
function lookupTmsForNormPos(nd) {
  return _interp(videoState.framesByNd, f => f.normPos, f => f.t_ms, nd);
}

// video time -> marker position (for playback sync)
function lookupNormPosForTms(t_ms) {
  return _interp(videoState.frames, f => f.t_ms, f => f.normPos, t_ms);
}
```

`_interp(arr, keyFn, valFn, target)` does binary search on `arr` by `keyFn`, then linear interpolation between the two surrounding entries using `valFn`.

#### Blob buffering

Videos up to 100 MB are fully fetched into a browser Blob and loaded via `URL.createObjectURL()`. This puts the entire file in memory so `video.currentTime = X` resolves instantly (no HTTP round-trip). Combined with one-keyframe-per-second encoding (`-g FPS` in ffmpeg), seeking while paused feels immediate.

For videos > 100 MB, the `video.src` points directly at the streaming endpoint. The browser uses HTTP Range requests to fetch byte ranges on seek. This is slower but doesn't require downloading the full file.

Previous blob URLs are revoked on each lap switch to prevent memory leaks.

### Common modification patterns

**Adding a new field to the sidecar:**
1. `video_recorder.py` `_record_sample()` — add the field to the entry dict
2. `video_recorder.py` `_write_sidecar()` — no change needed (writes all entries)
3. `index.html` `buildSyncLookups()` — filter for the new field in the `valid` filter
4. Add lookup function if needed

**Changing sync direction or behavior:**
- Edit `syncVideoFromMarker()` for marker->video
- Edit `_videoRafLoop()` for video->marker
- The `source` tag in `updateMarker(nd, forceTrack, source)` controls which direction fires

**Changing buffering limit:**
- `MAX_BLOB_BYTES` constant in `loadVideoForLapIdx` (currently `100 * 1048576`)

**Adding a new session ID format:**
- `_session_blob_variants()` in `telemetry-comparison/main.py` — add a new variant generation rule
- Both `_find_video_paths()` and `stream_video()` iterate the variants automatically
