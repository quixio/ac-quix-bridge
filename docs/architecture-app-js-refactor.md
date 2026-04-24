# Architecture: Telemetry Explorer `app.js` ES-module refactor

## What it does

Splits `telemetry-comparison/static/app.js` (previously 1452 lines) into six focused ES modules plus a thin bootstrap, with zero runtime behaviour change. The page now loads `app.js` as `<script type="module">`, which imports from `modules/state.js`, `modules/data.js`, `modules/sync.js`, `modules/selections.js`, `modules/charts.js`, and `modules/video.js`. No bundler was introduced; the browser resolves imports natively. The three sibling files `modules/toast.js`, `modules/track-map.js`, and `modules/video-overlay.js` remain classic scripts (`sourceType: 'script'`) to preserve their existing global-scope contract with inline `onclick=` handlers on `onZoomChange`, `toggleVideoFloat`, `setCombinedTab`, etc.

## Key decisions

- **Sync lives in one module.** `sync.js` owns the entire marker↔video bidirectional loop as a single unit: `updateMarker`, `syncVideoFromMarker`, `_onVideoFrame`, `_videoRafLoop`, `_startVideoSync`, `_stopVideoSync`, `wireVideoElement`, `buildSyncLookups`, `lookupTmsForNormPos`, `lookupNormPosForTms`, `highlightVideoLapTrace`, `clearTraceHighlight`, and `_highlightAnnotations`. The feedback-loop guard (`source === 'drag' | 'video' | undefined`) never crosses a file boundary. `charts.js` imports `updateMarker` and `video.js` imports `buildSyncLookups` + `highlightVideoLapTrace`; neither imports from the other. This matches the primary risk-mitigation requirement in spec §5 and §8.
- **`updateReadout` moved from charts.js to sync.js.** The spec originally listed `updateReadout` under `charts.js` (§6.4). Keeping it there would have required `sync.js` to import from `charts.js`, creating a sync↔charts cycle. Since `updateReadout` is tiny (10 lines), only called from `updateMarker`, and reads the same `markerPosition` state the sync loop already owns, it belongs in sync.js. This is a documented deviation from §6.4 in favour of the higher-priority "no circular imports" rule in §5.
- **Single canonical state.** `state.js` exports `appState` and `videoState` as object literals; every other module imports them by named binding. Because ES modules evaluate once per URL, all importers hold the same object reference and mutations are visible everywhere. The `videoState` shape is byte-identical to the pre-refactor version and matches `.claude/skills/video-seeking/SKILL.md`.
- **Classic-script bridge via `window.*`.** The non-module `track-map.js` still reads `trackData`, `trackConfig`, `markerPosition`, `trackZoom` as implicit globals and writes `trackBaseRange`, `trackZoom`, `window._markerTraceIdx`. `state.js` seeds these on `window` at module evaluation so the first read from `track-map.js` (which runs before the module bootstrap calls `renderTrackMap`) sees defined values. `sync.js` also exposes `window.updateMarker` because `track-map.js:205` calls `updateMarker(markerPosition, true)` at the end of `renderTrackMap` and has no way to `import`.
- **Inline-HTML handlers re-exposed per module.** Each module that owns a handler assigns it to `window` at the bottom of the file: `selections.js` publishes `addRow`, `removeRow`, `onPartChange`, `toggleAllLaps`, `toggleCat`; `charts.js` publishes `plot`, `toggleCornerOverlay`; `video.js` publishes `onVideoLapChange`, `onVideoSpeedChange`; `app.js` publishes `togglePanel`, `toggleTopbarPanel`. Handlers already owned by classic scripts (`toggleVideoFloat`, `setCombinedTab` in `video-overlay.js`; `onZoomChange` in `track-map.js`) were not touched.
- **No bundler.** Browsers fetch sibling modules in parallel; the seven-file fan-out adds no perceptible startup latency for a LAN or CDN origin, and keeping the FastAPI static-asset deployment model unchanged avoids build-tool sprawl.
- **ESLint config split by source type.** `eslint.config.js` gained a second flat-config block that matches exactly the seven ESM files with `sourceType: 'module'`. The classic-script block is unchanged and still covers `toast.js`, `track-map.js`, `video-overlay.js`. Both blocks keep `no-undef` off because the module↔classic bridge relies on implicit globals that lint cannot introspect.

## Module graph

```
                   ┌──────────┐
                   │ state.js │  (constants, appState, videoState, window bridge)
                   └────┬─────┘
           ┌────────────┼────────────┬─────────────┐
           ▼            ▼            ▼             ▼
      ┌─────────┐  ┌─────────┐  ┌──────────┐  ┌──────────┐
      │ data.js │  │ sync.js │  │selections│  │ video.js │
      └────┬────┘  └────┬────┘  │   .js    │  └────┬─────┘
           │            │       └────┬─────┘       │
           │            ▲            │             │
           │            └────────────┤             │
           │                         │             │
           ▼                         ▼             │
      ┌──────────────────────────────────┐         │
      │           charts.js              │◀────────┘
      │  imports: state, data, sync,     │
      │           selections, video      │
      └────────────┬─────────────────────┘
                   ▼
               ┌────────┐
               │ app.js │  (bootstrap IIFE, window.togglePanel, window.toggleTopbarPanel)
               └────────┘
```

No cycles. `sync.js` is a sink for `charts.js` and `video.js`; neither imports from the other. `app.js` imports from every module but nothing imports from it.

## Data flows

**Plot button click:**

```
[user] → window.plot() → charts.plot()
  ├─ selections.getSelections(), selections.getActiveSignals()
  ├─ data.fetchTelemetry(sel, signals) × N
  ├─ Plotly.newPlot per signal; writes to appState.plotDivs/Traces/Signals
  ├─ charts.attachMarkerDrag(plotDiv) — captures mouse; calls sync.updateMarker(..., 'drag')
  ├─ charts.linkXAxes(appState.plotDivs)
  ├─ sync.updateMarker(appState.markerPosition, true) — paints initial annotations + track dot
  └─ video.populateVideoLapPicker(selections)
       └─ video.loadVideoForLapIdx(0)
            ├─ fetch /api/video/{sid}/{lap}  — metadata + sidecar
            ├─ sync.buildSyncLookups(meta.sync) → videoState.frames, framesByNd
            ├─ sync.highlightVideoLapTrace(sel.label)
            ├─ HEAD /api/video/.../mp4 — size check
            ├─ if ≤ 100 MB: GET then URL.createObjectURL → video.src
            └─ else: video.src = meta.mp4_url (stream)
```

**Marker drag (paused video → seek):**

```
mousedown on any chart → charts.attachMarkerDrag handler
  → sync.updateMarker(nd, true, 'drag')
     ├─ state.setMarkerPosition(nd) — writes appState.markerPosition AND window.markerPosition
     ├─ sync.syncVideoFromMarker(nd, 'drag')
     │   ├─ if videoState.isPlaying → videoState.element.pause()
     │   ├─ t_ms = sync.lookupTmsForNormPos(nd)
     │   └─ if |currentTime - target| > 0.015 s → videoState.element.currentTime = target
     ├─ updates track dot via window.trackPointAtNorm + Plotly.restyle
     ├─ per-plot: recomputes annotations, Plotly.relayout with new shapes[0].x0/x1
     └─ sync.updateReadout() — rewrites #readout-pos-text
```

**Video playing (RVFC path → marker):**

```
<video> play event (wired once by sync.wireVideoElement)
  → videoState.isPlaying = true
  → sync._startVideoSync(v) → v.requestVideoFrameCallback(sync._onVideoFrame)

each displayed frame:
  sync._onVideoFrame(now, metadata)
    ├─ nd = sync.lookupNormPosForTms(metadata.mediaTime × 1000 × timeScale)
    ├─ sync.updateMarker(nd, true, 'video')  ← 'video' tag prevents echo into syncVideoFromMarker
    └─ if still playing → v.requestVideoFrameCallback(sync._onVideoFrame) (re-register)
```

**Native seek while paused:**

```
<video> seeked event (wired once by sync.wireVideoElement)
  → if isPlaying or no frames → bail
  → nd = sync.lookupNormPosForTms(v.currentTime × 1000 × timeScale)
  → sync.updateMarker(nd, true, 'video')  ← suppresses re-seek
```

**Initial page load (deep-link fast path):**

```
bootstrap IIFE in app.js
  ├─ sync.wireVideoElement()  — attaches <video> listeners once
  ├─ renders "loading..." placeholder row
  ├─ reads URLSearchParams; if any PART_COLS present → deep-link path
  │    ├─ Promise.all(data.fetchSessions(defaults), loadChannels(), data.fetchTrack())
  │    ├─ selections.addRow(defaults)
  │    └─ background data.fetchSessions() → refresh dropdowns via populateDropdowns
  └─ else → full data.fetchSessions() up-front → selections.addRow(null)
```

## File inventory

| File | Action | Lines | Purpose |
|------|--------|-------|---------|
| `telemetry-comparison/static/app.js` | Rewrote | 174 | Thin ESM bootstrap: wires modules, owns togglePanel/toggleTopbarPanel, runs deep-link vs direct-access init. |
| `telemetry-comparison/static/modules/state.js` | Created | 184 | Single source of truth for `appState`, `videoState`, all constants, and the `window.*` bridge to classic scripts. |
| `telemetry-comparison/static/modules/data.js` | Created | 156 | Pure fetchers (`fetchSessions`, `fetchTrack`, `fetchChannels`, `fetchTelemetry`) and math helpers (`downsample`, `interpolateAt`, `_interp`, `getDistinctValues`). |
| `telemetry-comparison/static/modules/selections.js` | Created | 282 | Row/dropdown/lap-picker UI + channel chip rendering. Exposes `window.addRow`, `removeRow`, `onPartChange`, `toggleAllLaps`, `toggleCat`. |
| `telemetry-comparison/static/modules/sync.js` | Created | 444 | Entire marker↔video loop. Exposes `window.updateMarker` for the classic `track-map.js`. |
| `telemetry-comparison/static/modules/charts.js` | Created | 276 | Plotly lifecycle: `plot`, `attachMarkerDrag`, `linkXAxes`, corner overlay, `setStatus`. Exposes `window.plot`, `window.toggleCornerOverlay`. |
| `telemetry-comparison/static/modules/video.js` | Created | 243 | Video lap loading + picker UI (no sync logic). Exposes `window.onVideoLapChange`, `window.onVideoSpeedChange`. |
| `telemetry-comparison/static/index.html` | Modified | — | Exactly one change: `<script src="/static/app.js">` → `<script type="module" src="/static/app.js">`. The three classic `<script src>` tags above it are untouched. |
| `telemetry-comparison/eslint.config.js` | Modified | — | Added a second flat-config block matching `static/app.js` + the six `static/modules/*.js` ESM files with `sourceType: 'module'`. |
| `docs/architecture-app-js-refactor.md` | Created | — | This document. |

## Integration with neighbouring features

- **`.claude/skills/video-seeking/SKILL.md`** — the `videoState` shape, the `source` tag semantics, the 15 ms seek threshold, the RVFC/rAF fallback split, the 100 MB blob-buffer cutoff, and the `buildSyncLookups` dual-sort pattern are all preserved unchanged. The SKILL doc points at "`index.html`" for the frontend file — that reference now maps to the module set, primarily `sync.js` + `video.js`. Update the SKILL doc's "Key files" row when someone next revises the skill.
- **`modules/track-map.js`** — still reads `trackData`, `trackConfig`, `markerPosition`, `trackZoom` via the implicit-globals contract. Still calls `updateMarker(markerPosition, true)` at the end of `renderTrackMap`. Works because `state.js` seeds the globals and `sync.js` exposes `window.updateMarker`.
- **`modules/video-overlay.js`** — unchanged. Relocates `#video-controls` and `#track-readout` between docked and floating modes; relies on IDs that are preserved, and on `videoState.element` pointing at the same DOM node after reparenting (no change to that invariant).
- **`modules/toast.js`** — unchanged. Continues to expose `showToast` as a global; `app.js` reads it via `window.showToast` at the three previously existing call sites.

## Verification performed

1. `node --check` syntax pass on all seven ESM files — clean.
2. `npm run lint` — 0 errors, 14 warnings (all pre-existing unused-`_` warnings on classic scripts and one on my new `sync.js` that matches the existing `catch (_)` pattern).
3. `prettier --check` on the six newly created module files — clean. The pre-existing failures on `app.js`, `index.html`, `toast.js`, `track-map.js`, `video-overlay.js`, `styles.css` are unchanged from before the refactor (out of scope for this branch).
4. Node harness (`.tmp/smoke-import.mjs`) loads the full module graph with stubbed browser globals and asserts: no syntax/import/TDZ errors; all 12 inline-HTML handlers (`plot`, `addRow`, `removeRow`, `onPartChange`, `toggleAllLaps`, `toggleCat`, `toggleCornerOverlay`, `togglePanel`, `toggleTopbarPanel`, `onVideoLapChange`, `onVideoSpeedChange`, `updateMarker`) are set on `window`; `appState` and `videoState` are shared by reference across every importer.
5. Grep sweep confirms exactly one declaration of `appState` and one of `videoState`, both in `state.js`.
6. Browser-level smoke test (spec §8.1 six-step checklist against a running Telemetry Explorer) — NOT run from this environment; requires Quix Cloud deploy or an `ac-telemetry-source` feeding live data. Ludvík QAs this path manually.
