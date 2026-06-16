# Backlog

Loose scoping + open questions captured for later. Pick up by feeding the relevant section back to Maestro to finalize a spec.

Last updated: 2026-04-15.

> **Note:** A fourth topic ("Layout redesign — fixed top bar") was scoped and spec'd on 2026-04-15 and is now in active implementation. See `dev-planning/layout-redesign.md` and mockup at `dev-planning/layout-mockup.html`. Not in backlog anymore — removed to avoid confusion.

---

## Topic 1 — Track map appears vertically mirrored (Nurburgring, likely others)

**Symptom:** Nurburgring renders vertically mirrored on the deployed Telemetry Explorer (https://telemetryexplorer-quixdev-acquixbridge-videostreaming.deployments-dev.quix.io/).

**Root cause (investigated):** Renderer maps `{x: p.x, y: p.z}` with no transform. AC track authors pick arbitrary world-axis orientation, so this will recur per new track. A global `y→-z` flip would break tracks that happen to be authored the other way.

**Relevant code:**
- `telemetry-comparison/main.py:288-348` — `_load_track_csv()`
- `telemetry-comparison/static/index.html:624-787` — `renderTrackMap()` (axes set at 762-774)
- `telemetry-comparison/tracks/<track>/layout_sprint_a.csv` — current sole track: `ks_nurburgring/`

**Maestro's recommendation:** per-track orientation block in the track config (merge with Topic 3), not a global flip.

**Open questions for Ludvik:**
1. Is "mirrored" relative to AC's in-game minimap or to a north-up real-world map?
2. Fix scope — (a) `flip_z` flag only, (b) full `{rotation_deg, flip_x, flip_z}` block, or (c) in-UI flip button? Maestro leans (b).
3. Config location — merge into Topic 3 per-track config, or keep in `tracks_config.json` for now?

---

## Topic 2 — Video sync has visible gaps; investigate + consider burning normPos into frames

**Symptom:** Visible drift between marker position and video frame during playback/drag.

**Current mechanism:**
- Sidecar JSON per lap (`{session_id}_lap{N:03d}.sync.json`) with `frames[] = {idx, t_ms, wall_ms, normPos}`
- Generated in `ac_video_streaming/video_recorder.py:238-326` — `finish_lap()`
- ffmpeg encode at `video_recorder.py:125-137` — no text overlay filter; `-g FPS` → ~1 keyframe/sec
- Frontend lookups in `telemetry-comparison/static/index.html:1655-1671` — `buildSyncLookups()`
- Sync loop at `index.html:1696-1733` — mode-switch between marker-drag-seeks-video and video-play-drives-marker

**Maestro's hypotheses, ranked by likely severity:**
- **A (high):** Browser seek snaps to preceding keyframe → marker-drag can show a frame up to ~1 s stale. **Most likely visible cause.**
- **B (med):** 5 Hz sidecar + linear interp is noisy through braking / accel zones.
- **C (med):** `normPos` wraps 1.0 → 0.0 at start/finish; binary search on sorted `framesByNd` can snap wrong.
- **D (low):** Frame-grab vs. normPos-read latency adds per-frame jitter.

**Overlay proposal:** Useful for debugging only — sidecar already has the data, so runtime sync doesn't benefit. Implement as PIL/numpy draw before the ffmpeg pipe, gated by `OVERLAY_DEBUG=1`.

**Open questions for Ludvik:**
4. Should the spec also *fix* keyframe seeking (drop `-g` to ~4, or `-forced-idr` at sidecar sample points) or only diagnose? Fixing increases file size but directly kills the main drift source.

Everything else for Topic 2 is ready to spec without more input from Ludvik.

---

## Topic 3 — Corner designation storage + merged-CSV-as-dynamic-config

**Current state:**
- Corners are auto-computed from radius severity in `main.py:313-342`, labeled `T1..Tn`.
- No human-readable corner names (no "Karussell", no "Eau Rouge").
- Track CSV has geometry + radius/gradient/width only; `tracks_config.json` holds global thresholds.

**Ludvik's vision:** single per-track config (geometry + named corners + other track metadata) injected as dynamic config at deploy time.

**Maestro's recommendation:** keep tool-regenerable geometry CSV separate from hand-curated corner names (separate `corners.json` sidecar merged on `normalizedDistance` range at load time).

**Open questions for Ludvik:**
5. Schema — (a) extend geometry CSV with `corner_name` column, or (b) separate `corners.json` sidecar merged at load time? Maestro leans (b).
6. Source of corner names — manually curated or imported from AC mods / external data?
7. Fallback when some corners are named and others aren't — unnamed ones keep `T9..T20`, show nothing, or `T9 (unnamed)`?
8. "Inject as dynamic config" — which mechanism?
   - (a) Quix deploy var pointing at a URL, fetched at startup
   - (b) env var with a mounted path
   - (c) Quix config topic payload
   - (d) git-committed path per deployment
9. V1 metadata scope — just geometry + corner names + orientation, or also sector boundaries, DRS zones, pit entry/exit, canonical lap length?

---

---

## Topic 4 — Corner name labels on zoomed track map

**Status:** Prototyped and removed — needs better collision avoidance before shipping.

**What:** When the track map is zoomed in (>=2x), show corner names as text labels near the corner badges. Labels should be placed outside the track boundary using the track tangent + corner direction to compute placement.

**Problems found during prototype:**
- Labels overlap the track in dense corner clusters (Mercedes Arena section has 4+ corners close together)
- Collision avoidance by filtering close corners loses information
- Pixel vs data-space offset doesn't scale well across zoom levels

**Ideas to explore:**
- Place labels in data coordinates offset along the outside-normal of each corner
- Use a force-directed layout to push overlapping labels apart
- Only show label for the corner nearest to the current marker position
- Show label on hover of the corner badge instead of always-on

**Relevant code reference:** The prototype used `_buildCornerAnnotations()` in `renderTrackMap()` returning Plotly annotations with `axref/ayref: 'x'/'y'` for data-space positioning. Removed 2026-04-16.

---

## Meta

10. Combine Topics 1 + 3 into one "track-config-format" spec? Keep Topic 2 separate as "video-sync-gaps"? (Maestro's recommendation.)

## Resuming

Maestro agent ID from the scoping conversation: `a69d1d2309c31718d` (valid only while that agent is still alive — if stale, re-invoke Maestro and point it at this file).
