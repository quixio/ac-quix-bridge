# Telemetry Explorer — Layout Redesign

**Status:** Draft
**Project:** ac-quix-bridge
**Created:** 2026-04-15
**Planned with:** Maestro

## 1. Summary

Restructure the Telemetry Explorer (`telemetry-comparison/static/index.html`) from a right-sidebar layout to a top-bar layout. The fixed sidebar (track map, video, readout) becomes a fixed top bar with a 65/35 video-left / map-right grid. The per-trace readout becomes a sticky strip below the top bar. Charts, session picker, and signal picker scroll normally underneath. No backend changes. No new features. All existing functionality (video sync, marker drag, corner overlays, URL deep-linking, cascading filters) must survive intact.

## 2. Goals

- Move video panel and track map into a fixed top bar, freeing full page width for charts.
- Replace the 400px right-margin body layout with a top-margin layout.
- Reduce per-chart height and legend footprint so more signals are visible above the fold.
- Simplify the legend label to show only lap/session identifiers (not experiment/track/car/driver every time).
- Change the marker line color from red to white for better contrast against the multi-severity corner overlays.
- Preserve every existing interaction, data flow, event handler, and URL contract.

## 3. Non-goals

- No backend changes (`telemetry-comparison/main.py` is untouched).
- No new features (no sticky signal-picker, no floating panel, no mobile layout).
- No changes to data APIs, Kafka topics, or telemetry pipeline.
- No responsive layout for viewports below 800px. Primary target is desktop >=1200px.
- No changes to the track map's multi-severity coloring logic, corner overlay toggle, or Plotly track-map rendering code beyond container size.
- No changes to video sync logic, video buffering, rAF loop, or sidecar lookup code.

## 4. User stories / scenarios

**S1: Page loads at 1920x1080.** The top bar occupies the top ~340px. Video panel fills the left 65%, track map the right 35%. Below the bar is a sticky readout strip ("42.3% @ 8642m"). Below that, session picker, signal picker, and charts scroll normally with full page width.

**S2: User scrolls down to view later charts.** The top bar stays fixed. The readout strip sticks directly below it. Session/signal pickers scroll away.

**S3: User drags the marker on a chart.** White vertical marker line updates on all charts. Track map red dot moves. Video seeks (if paused). Readout strip updates position text. Identical behavior to current production except marker line is white instead of red.

**S4: Video is playing.** Video rAF loop drives the marker line (white), red dot, and readout. No change from current sync behavior.

**S5: User collapses the video panel via the minus button.** Video panel body hides. Top bar height may shrink (CSS handles overflow). Chart content gains vertical space.

**S6: User collapses the track map panel.** Track map and zoom slider hide. Same pattern as video collapse.

**S7: Multi-session comparison.** Two selection rows from different sessions. Legend labels read `S1-L1`, `S1-L2`, `S2-L1` etc. Chart legends are narrower (right margin 60px, font 8px).

**S8: Single-session comparison.** One selection row with multiple laps. Legend labels read `L1`, `L2`, `L3`.

**S9: URL deep-link.** `?environment=quix-dev&track=ks_nurburgring` pre-fills the first selection row. No change from current behavior.

**S10: 1366x768 laptop viewport.** Top bar and charts render acceptably dense. No horizontal overflow. Session picker wraps its 7 dropdowns into two lines.

## 5. Proposed design

Convert the fixed right sidebar `.track-map-panel` into a fixed top bar `.topbar` using CSS Grid. The mockup at `dev-planning/layout-mockup.html` (screenshot: `layout-proposal-v6-fullpage.png`) is the visual source of truth for the target layout.

The approach consolidates the three formerly-stacked sidebar elements (map, readout, video) into a horizontal arrangement that reclaims the full page width for charts. The readout becomes a minimal sticky strip. This is strictly a CSS/HTML restructure with three small JS edits (legend labels, marker color, Plotly constants).

## 6. File inventory

| File | Change type | Rationale |
|------|-------------|-----------|
| `telemetry-comparison/static/index.html` | CSS + HTML restructure + 3 JS edits | The only production file affected. All layout, styles, markup, and the three JS tweaks live here. |

No other files are touched. `telemetry-comparison/main.py` serves this file unchanged.

## 7. CSS changes

All CSS lives in the `<style>` block at lines 8-451 of the production file.

### 7.1 New CSS variable

Add to `:root` (line 9):

```css
--topbar-height: clamp(260px, 35vh, 380px);
```

Note: the mockup uses a fixed `340px` for simplicity. Production must use the `clamp()` to handle viewport range 768px-1440px+. The three values are:
- **260px** minimum — ensures video is still watchable on short viewports.
- **35vh** preferred — scales proportionally.
- **380px** maximum — prevents the bar from eating half the screen on tall monitors.

### 7.2 Body rule changes (line 21-28)

**Remove:**
```css
padding: 1.5rem;
padding-right: 400px;
```

**Replace with:**
```css
padding-top: var(--topbar-height);
```

### 7.3 Media query at line 29-31

**Remove entirely:**
```css
@media (max-width: 1200px) {
  body { padding-right: 300px; }
}
```

This media query only existed to shrink the sidebar width. No longer relevant.

### 7.4 Media query at line 430-432

**Remove entirely:**
```css
@media (max-width: 1200px) {
  .track-map-panel { width: 280px; }
}
```

Same rationale — the sidebar is gone.

### 7.5 New `.topbar` rule (add after body rule)

```css
.topbar {
  position: fixed;
  top: 0; left: 0; right: 0;
  height: var(--topbar-height);
  display: grid;
  grid-template-columns: 65fr 35fr;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  box-shadow: 0 4px 12px rgba(0,0,0,0.4);
  z-index: 100;
}
```

### 7.6 New `.topbar-panel` rule

```css
.topbar-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
}
```

### 7.7 New `.topbar-head` rule

```css
.topbar-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px 6px;
  border-bottom: 1px solid var(--border);
}
```

### 7.8 New `.topbar-body` rule

```css
.topbar-body {
  flex: 1;
  padding: 8px 12px 10px;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  min-height: 0;
}
```

### 7.9 Video panel adaptations for top-bar context

The video player and its controls move from inside `.track-map-panel` to the left `.topbar-panel`. The existing `.video-panel`, `.video-player`, `.video-empty`, `.video-controls`, `.video-speed-label`, `.video-status` classes (lines 364-428) remain functionally identical. The key change is that the video `<video>` element must fill the `.topbar-body` space vertically rather than being constrained by a 360px-wide sidebar:

**Modify `.video-player` (line 382):**
```css
.video-player {
  width: 100%;
  flex: 1;
  min-height: 0;
  background: #000;
  border-radius: 6px;
  display: block;
  object-fit: contain;
}
```

The `aspect-ratio: 16/9` is removed because the video now fills available height within the flex container rather than being width-constrained.

**Modify `.video-empty` (line 389):**
Same change — remove `aspect-ratio: 16/9`, add `flex: 1; min-height: 0;`.

### 7.10 New `.readout-strip` rule

```css
.readout-strip {
  position: sticky;
  top: var(--topbar-height);
  z-index: 50;
  background: rgba(15,17,23,0.85);
  backdrop-filter: blur(6px);
  border-bottom: 1px solid var(--border);
  padding: 8px 20px;
  display: flex;
  gap: 1.2rem;
  align-items: center;
  font-size: 0.72rem;
}
.readout-strip .readout-pos {
  color: var(--accent);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
```

### 7.11 Remove `.track-map-panel` and descendants (lines 248-362)

The following CSS rules become dead code and must be removed:

- `.track-map-panel` (line 248-261)
- `.track-map-panel.collapsed .zoom-row` (line 280)
- `.track-map-panel .panel-title` (line 281-286)
- `.track-map-panel .toggle-btn` (line 287-294)
- `.track-map-panel .toggle-btn:hover` (line 295)
- `.track-map-panel.collapsed #track-map` (line 296)
- `.track-map-panel.collapsed .track-readout` (line 297)
- `.readout-header` (line 298-307)
- `.track-map-panel.collapsed .readout-header` (line 308)
- `.track-readout` (line 309-313)
- `.readout-table` and all `.readout-table *` rules (lines 314-362)
- `.track-map-panel.collapsed .video-panel` (line 428)

### 7.12 New collapse behavior for top-bar panels

```css
.topbar-panel.collapsed .topbar-body { display: none; }
```

This is the same pattern as `.panel.collapsed .panel-body` (line 71). When a top-bar panel is collapsed, its body (video/map content) hides but the header row stays visible.

### 7.13 Scrolling content wrapper

Add a `.content` wrapper rule:
```css
.content {
  padding: 16px 20px 40px;
}
```

### 7.14 H1 adjustment (line 32)

Move `h1` styling under `.content` context if desired, or just adjust:
```css
h1 { font-size: 1.15rem; font-weight: 600; margin-bottom: 0.9rem; }
```

Reduced from `1.4rem` to `1.15rem` to match the denser layout.

### 7.15 Track map in top-bar context

The `#track-map` div currently has an inline `style="height: 360px;"` (line 463). In the new layout it must fill the `.topbar-body` flex container instead:

```css
.map-panel .topbar-body { padding: 6px 8px 8px; gap: 0.3rem; }
#track-map { flex: 1; min-height: 0; }
```

The inline `style="height: 360px;"` must be removed from the HTML (see section 8).

## 8. HTML structural changes

All changes are within `<body>` (lines 453-1793).

### 8.1 Remove old sidebar block (lines 458-496)

The entire `<div class="track-map-panel" id="track-panel">...</div>` is removed. Its contents are split into two new top-bar panels and the readout strip (see below).

### 8.2 Add new top bar (insert before everything else in `<body>`)

```html
<div class="topbar" id="topbar">
  <!-- VIDEO panel (left, 65%) -->
  <div class="topbar-panel" id="topbar-video">
    <div class="topbar-head">
      <div class="panel-title">Video</div>
      <div class="video-controls" id="video-controls" style="display:none">
        <select id="video-lap-select" class="video-lap-select" disabled
                onchange="onVideoLapChange(this.value)">
          <option value="">— pick laps and Plot —</option>
        </select>
        <label class="video-speed-label">Speed
          <select id="video-speed" class="video-lap-select"
                  onchange="onVideoSpeedChange(this.value)">
            <option value="1" selected>Realtime</option>
            <option value="2">2x</option>
            <option value="5">5x</option>
          </select>
        </label>
        <div id="video-status" class="video-status"></div>
        <button class="panel-collapse-btn"
                onclick="document.getElementById('topbar-video').classList.toggle('collapsed')"
                title="Collapse">−</button>
      </div>
    </div>
    <div class="topbar-body">
      <video id="video-player" class="video-player" controls preload="metadata"
             style="display:none"></video>
      <div id="video-empty" class="video-empty">No lap selected</div>
    </div>
  </div>

  <!-- TRACK MAP panel (right, 35%) -->
  <div class="topbar-panel map-panel" id="topbar-map">
    <div class="topbar-head">
      <div class="panel-title">Track Map</div>
      <button class="panel-collapse-btn"
              onclick="document.getElementById('topbar-map').classList.toggle('collapsed')"
              title="Collapse">−</button>
    </div>
    <div class="topbar-body">
      <div id="track-map"></div>
      <div class="zoom-row">
        <span>Zoom</span>
        <input type="range" id="track-zoom" min="1" max="8" step="0.1" value="1"
               oninput="onZoomChange(this.value)">
        <span class="zoom-val" id="track-zoom-val">1.0x</span>
      </div>
    </div>
  </div>
</div>
```

Key differences from old markup:
- Video controls (lap dropdown, speed dropdown, status text) move into the `.topbar-head` row alongside the collapse button, not below the video.
- The video `<video>` element and empty-state div go into `.topbar-body`.
- Track map div `#track-map` loses its inline `style="height: 360px;"` — height is now controlled by flex.
- The collapse buttons wire to `topbar-video` and `topbar-map` IDs respectively.
- All element IDs (`video-player`, `video-empty`, `video-controls`, `video-lap-select`, `video-speed`, `video-status`, `track-map`, `track-zoom`, `track-zoom-val`) are preserved exactly. JS code references these by ID and must not break.

### 8.3 Add readout strip (after top bar, before content)

```html
<div class="readout-strip" id="track-readout">
  <span class="readout-pos" id="readout-pos-text">0.0%</span>
</div>
```

This replaces the old `<div class="track-readout" id="track-readout">` that contained a `<table class="readout-table">`. The `id="track-readout"` is preserved because `updateReadout()` (line 916) references it.

### 8.4 Wrap scrolling content

Move the H1, session panel, signal panel, charts div, and status bar into a `<div class="content">`:

```html
<div class="content">
  <h1>Telemetry <span>Explorer</span></h1>
  <!-- #panel-sessions (unchanged internally) -->
  <!-- #panel-signals (unchanged internally) -->
  <div class="charts" id="charts"></div>
  <div class="status-bar" id="status"></div>
</div>
```

The H1 currently sits at line 455 above the sidebar. It moves inside `.content`, below the readout strip.

### 8.5 Elements that do NOT move

- `#panel-sessions` (lines 499-511) — internal structure untouched.
- `#panel-signals` (lines 514-522) — internal structure untouched.
- `#charts` (line 525) — untouched.
- `#status` (line 526) — untouched.

## 9. JS changes

All JS lives in the `<script>` block starting at line 528. Changes are minimal and surgical.

### 9.1 `PLOTLY_LAYOUT` constant (lines 545-554)

Three value changes within the existing object:

| Key | Old value | New value | Reason |
|-----|-----------|-----------|--------|
| `legend.font.size` | `10` | `8` | Narrower legend to fit 60px right margin |
| `margin.r` | `150` | `60` | Reclaim chart width now that sidebar is gone |
| `height` | `350` | `240` | Denser chart stacking; more signals visible |

The full new constant:
```js
const PLOTLY_LAYOUT = {
  paper_bgcolor: '#1a1d27',
  plot_bgcolor: '#1a1d27',
  font: { color: '#e2e8f0', size: 11 },
  legend: { orientation: 'v', x: 1.02, y: 1, font: { size: 8 } },
  margin: { t: 10, r: 60, b: 40, l: 55 },
  height: 240,
  xaxis: { color: '#8892a4', gridcolor: '#2d3047', zerolinecolor: '#2d3047' },
  yaxis: { color: '#8892a4', gridcolor: '#2d3047', zerolinecolor: '#2d3047', autorange: true },
};
```

### 9.2 Legend label builder in `getSelections()` (lines 1174-1177)

**Current code:**
```js
const label = [
  filters.experiment, filters.track, filters.carModel,
  `L${lap}`, `(${filters.driver || '?'})`
].filter(Boolean).join(' ');
```

**New logic:**

Determine at the top of `getSelections()` whether all selection rows share the same `session_id`. If yes, labels use the short format `L<lap>`. If no, labels use `S<sessionIdx>-L<lap>` where `sessionIdx` is 1-based, derived from the row's position among unique session IDs.

```js
function getSelections() {
  const result = [];
  let colorIdx = 0;

  // Collect all row session_ids first to detect multi-session
  const rows = document.querySelectorAll('.selection-row');
  const sessionIds = [];
  rows.forEach(row => {
    const filters = getRowFilters(parseInt(row.dataset.rowIdx));
    sessionIds.push(filters.session_id || '');
  });
  const uniqueSessions = [...new Set(sessionIds)];
  const multiSession = uniqueSessions.length > 1;

  rows.forEach((row, rowIdx) => {
    const filters = getRowFilters(parseInt(row.dataset.rowIdx));
    const sIdx = multiSession ? uniqueSessions.indexOf(sessionIds[rowIdx]) + 1 : -1;

    const checked = row.querySelectorAll('.lap-cb input:checked');
    checked.forEach(cb => {
      const lap = parseInt(cb.value);
      const label = multiSession
        ? `S${sIdx}-L${lap}`
        : `L${lap}`;
      result.push({
        key: { ...filters },
        lap,
        color: TRACE_COLORS[colorIdx++ % TRACE_COLORS.length],
        label,
      });
    });
  });
  return result;
}
```

This is the exact logic. The implementer should match this structure.

### 9.3 Marker line color (line 1328)

**Current:**
```js
line: { color: (trackConfig?.colors?.marker) || '#ef4444', width: 1.5, dash: 'solid' },
```

**New:**
```js
line: { color: '#ffffff', width: 1.5, dash: 'solid' },
```

The `trackConfig?.colors?.marker` fallback is removed entirely. The marker is always white. This is intentional: white stands out against the multi-colored corner overlays (blue/green/orange/red) far better than the old red which clashed with hairpin overlay color.

### 9.4 `updateReadout()` function (lines 915-927)

**Current implementation** builds a `<table class="readout-table">` inside `#track-readout`.

**New implementation** writes a simpler text string into the readout strip:

```js
function updateReadout() {
  const el = document.getElementById('readout-pos-text');
  if (!el) return;

  const trackPt = trackPointAtNorm(markerPosition);
  el.textContent = trackPt
    ? `${(markerPosition * 100).toFixed(1)}% @ ${trackPt.distance_m.toFixed(0)}m`
    : `${(markerPosition * 100).toFixed(1)}%`;
}
```

Note: the element ID changes from targeting `#track-readout` (the container) to `#readout-pos-text` (the text span inside it). The container `#track-readout` still exists for backward compat with any code that checks for its presence.

### 9.5 Top-bar collapse toggle wiring

The old sidebar collapse was a single inline `onclick` on line 461:
```js
onclick="document.getElementById('track-panel').classList.toggle('collapsed')"
```

The new markup has two separate collapse buttons (see section 8.2), each toggling their own panel ID (`topbar-video`, `topbar-map`). These are wired inline in the HTML. No new JS function is needed — the existing pattern is inline classList toggle.

The existing `togglePanel()` function (line 1376-1381) is for the session/signal panels and stays unchanged.

### 9.6 `_wireVideoElement()` (line 1735)

No code change. It references `document.getElementById('video-player')` which is preserved.

### 9.7 `renderTrackMap()` (line 624)

No code change. It references `document.getElementById('track-map')` which is preserved. The Plotly `responsive: true` config will handle the new container size automatically. The `height` in the Plotly layout for the track map is set dynamically from the container (the old inline `height: 360px` is gone), so `renderTrackMap` may need its layout height set to match the container. Review whether adding `autosize: true` to the track-map Plotly config is sufficient, or whether a fixed height needs to be computed.

### 9.8 Functions NOT touched

The following functions must not be modified (this is the implementer's don't-touch list):

- `fetchSessions()`, `loadChannels()`, `fetchTrack()`
- `renderTrackMap()` (beyond the autosize consideration in 9.7)
- `onZoomChange()`, `applyZoom()`
- `attachMarkerDrag()`, `updateMarker()` (except that it calls `updateReadout()` which is changed)
- `syncVideoFromMarker()`, `_videoRafLoop()`, `_wireVideoElement()`
- `loadVideoForLapIdx()`, `onVideoLapChange()`, `onVideoSpeedChange()`
- `populateVideoLapPicker()`, `buildSyncLookups()`
- `showVideoElement()`, `hideVideoElement()`, `setVideoStatus()`
- `toggleCornerOverlay()`, `linkXAxes()`
- `addRow()`, `removeRow()`, `onPartChange()`, `getRowFilters()`
- `toggleCat()`, `getActiveSignals()`
- `downsample()`, `interpolateAt()`
- `plot()` (except the `getSelections()` call within it, which is affected by 9.2 above)
- Init IIFE at line 1775

## 10. Test plan for Tester agent

### 10.1 Layout verification

| Test | Expected |
|------|----------|
| Load page at 1920x1080 | Top bar fixed at top. Video fills left ~65%. Track map fills right ~35%. Readout strip directly below. H1 + panels + charts scroll below. |
| Load page at 1440x900 | Top bar slightly shorter (clamp kicks in). All elements fit. No overflow. |
| Load page at 1366x768 | Top bar at minimum 260px. Charts still visible below. Usable but dense. |
| Scroll down 3 charts | Top bar stays fixed. Readout strip sticks below it. Session picker has scrolled away. |

### 10.2 Panel collapse

| Test | Expected |
|------|----------|
| Click video panel collapse button | Video body hides. Header row with lap/speed dropdowns stays. |
| Click track map collapse button | Map and zoom slider hide. Header row stays. |
| Collapse both | Only header rows visible in top bar. Maximum vertical space for charts. |
| Re-expand both | Panels restore to full content. |

### 10.3 Video sync (regression)

| Test | Expected |
|------|----------|
| Select laps, click Plot, pick a lap in video dropdown | Video loads in top-bar video panel. Status text shows size + sync points. |
| Play video | Marker line (white) moves on all charts. Red dot moves on track map. Readout strip updates. |
| Pause video, drag marker on chart | Video seeks to matching position. |
| Change playback speed to 2x | Video plays at 2x. Marker updates at accelerated rate. |

### 10.4 Marker and readout

| Test | Expected |
|------|----------|
| Drag marker on any chart | White vertical line on all charts. Red dot on map. Readout shows `42.3% @ 8642m` format. |
| Marker line visibility | White line (`#ffffff`) clearly visible against dark chart background and colored corner overlays. |

### 10.5 Legend labels

| Test | Expected |
|------|----------|
| Single session, 3 laps selected | Legend labels: `L1`, `L2`, `L3`. |
| Two sessions, 2 laps each | Legend labels: `S1-L1`, `S1-L2`, `S2-L1`, `S2-L2`. |
| Three sessions, 1 lap each | Legend labels: `S1-L1`, `S2-L1`, `S3-L1`. |

### 10.6 Chart layout

| Test | Expected |
|------|----------|
| Plot 4 signals | Each chart is 240px tall. Legend font is small (8px). Right margin is narrow (~60px). |
| Enable corner overlay on a chart | Corner bands + labels render correctly. No clipping from new margins. |
| Per-trace value annotations at marker | Dark bg boxes with per-trace colored text, stacked vertically. `+N` indicator if >6 traces. |

### 10.7 Preserved features (regression)

| Test | Expected |
|------|----------|
| Add Session button | New selection row appears with 7 dropdowns and color dot. |
| Remove session row (x button) | Row removed. |
| Cascade filters | Changing Environment filters downstream Rig/Experiment/etc. |
| "all" lap toggle | All laps check/uncheck. |
| Signal category "show all N" | Hidden signals in category expand/collapse. |
| URL deep-link `?environment=quix-dev&track=ks_nurburgring` | First row pre-filled with those values. |
| Track map multi-severity coloring | Hairpin (red), tight (orange), sweeper (green), straight (blue) all render. |
| Track map zoom slider | Zoom in/out works. Zoomed view centers on marker. |

## 11. Risks, constraints, and open questions

### Risks

**R1: Track map auto-sizing.** The current `renderTrackMap()` creates a Plotly chart at `height: 360px` (inline style). Removing the inline height and relying on flex + `responsive: true` should work, but Plotly sometimes needs an explicit height or a `Plotly.Plots.resize()` call after the container settles. The implementer should verify the map renders correctly and add a resize call after initial render if needed.

**R2: `clamp()` and video aspect ratio.** At the minimum 260px top-bar height, the video player will be short (~200px after header/controls). A 16:9 video at 200px height is ~355px wide, which fits the 65% column on 1200px+ screens. But on exactly 800px width (65% = 520px) at 260px bar height, the video will have black bars. This is acceptable per the non-goals (mobile/<800px is out of scope).

**R3: Scroll-to-change-signals friction.** With the session and signal pickers scrolling away, users viewing later charts must scroll back up to change signals or sessions. This is a known UX compromise, accepted for V1. A future follow-up could make the signal picker collapsible/floating, reusing the same `panel-collapse-btn` pattern.

### Open questions

**Q1: Video controls in collapsed state.** When the video panel is collapsed, should the lap dropdown and speed dropdown still be visible in the header row? The mockup places them in `.topbar-head`, so they stay visible when collapsed. Confirm this is correct. (Current assumption: yes, controls stay visible.)

**Q2: Readout content scope.** The current readout shows only `Position: 42.3% @ 8642m`. The mockup's readout strip also shows a hint "per-trace values render on each chart's marker". Should the hint text be in production? (Current assumption: no, the hint is mockup-only; production readout strip shows only the position text.)

## 12. Alternatives considered

**A: Keep right sidebar, widen charts.** Keep video and map on the right but narrow the sidebar to ~280px. Rejected: the sidebar was already tight at 360px; video at 280px wide is unwatchable. Charts still lose 280px of width.

**B: Floating panels.** Make video and map draggable/resizable floating windows. Rejected: significantly more JS complexity (drag, resize, z-index management, position persistence). Overkill for the current use case. Could revisit if users want to position panels freely.

**C: Tabbed top bar.** Single top-bar slot that tabs between video and map. Rejected: users need to see both simultaneously — the map shows where on track, the video shows the car at that point. Tabbing defeats the purpose.

## 13. References

- **Mockup HTML:** `dev-planning/layout-mockup.html`
- **Mockup screenshot:** `layout-proposal-v6-fullpage.png` (repo root)
- **Production file:** `telemetry-comparison/static/index.html` (1795 lines)
- **Video sync design:** `docs/video-sync-design.md`
- **Telemetry Explorer state:** Memory note from 2026-04-13 (track map + synced marker + annotations done)

## 14. Agent assignments

| Sub-task | Owner | Notes |
|----------|-------|-------|
| CSS restructure (sections 7.1-7.15) | ArchDev | Bulk of the work. Remove old sidebar CSS, add topbar/readout rules, modify body padding. |
| HTML restructure (sections 8.1-8.5) | ArchDev | Move elements, preserve all IDs. |
| JS edits (sections 9.1-9.4) | ArchDev | Three surgical changes: PLOTLY_LAYOUT, getSelections labels, marker color, updateReadout. |
| Visual polish pass | FrontEndEsthetic | After ArchDev delivers: verify spacing, contrast, font sizes match mockup. Check video panel at min/max clamp heights. Verify readout strip blur effect. |
| Regression test suite (section 10) | Tester | All scenarios in section 10. Focus on video sync, marker drag, collapse toggles, legend label formats, URL deep-link. |
