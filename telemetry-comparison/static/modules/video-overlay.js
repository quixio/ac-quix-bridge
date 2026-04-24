/**
 * Combined overlay controller — dock/float toggle for the unified
 * Video + Map panel.
 *
 * The combined panel (`#combined-panel`) owns two panes: `#video-panel` and
 * `#topbar-map`. It lives between two slots:
 *   - `#video-dock-slot`  : the topbar row (docked mode — both panes visible
 *                            side-by-side in a 65/35 flex).
 *   - `#video-float-slot` : a `position:fixed` container that interact.js
 *                            drags + resizes (floating mode — only the
 *                            active-tab pane visible).
 *
 * Mode lives on `<body data-video-mode="docked|floating">`.
 * Active tab lives on `<section id="combined-panel" data-active-tab="video|map">`.
 *
 * Geometry + mode + tab are persisted to
 * `localStorage['telemetryExplorer.videoOverlay.v1']`:
 *   { mode, x, y, width, height, activeTab }
 *
 * The `<video>` element with id `#video-player` is a grandchild of
 * `#combined-panel` and moves with it on reparenting. Modern browsers
 * (Chromium, Firefox) preserve playback state across reparenting; the video
 * sync contract in `.claude/skills/video-seeking/SKILL.md` stays intact because
 * `videoState.element` still points at the same DOM node.
 *
 * Plain (non-module) script so `toggleVideoFloat` and `setCombinedTab` are
 * globally accessible from the inline onclick= in index.html.
 */

const VIDEO_OVERLAY_STORAGE_KEY = 'telemetryExplorer.videoOverlay.v1';
// Legacy key kept only for cleanup on init. Round 2.0 persisted user choice
// across reloads; Round 2.1 scopes it to the current page session (see
// _userChoseModeSession below). We still actively remove the old key on
// init so stale "true" values from prior sessions don't lock users out of
// auto-float.
const VIDEO_OVERLAY_LEGACY_USER_CHOSE_KEY = 'telemetryExplorer.videoOverlay.userChoseMode';
const VIDEO_OVERLAY_DEFAULTS = {
  mode: 'docked',
  x: 0,
  y: 0,
  width: 480,
  height: 270, // 16:9 of 480
  activeTab: 'video',
};
const MIN_VIDEO_WIDTH = 320;
const MIN_VIDEO_HEIGHT = 180;
const SNAP_GRID = 8;

// Tabbed-float: viewport thresholds for "too cramped to dock usefully".
//
// Round 1 complaints (Nitpicker): the spec's original (1280, 760) missed the
// classic laptop 1366×768 by width (1366 ≥ 1280) AND by height (768 ≥ 760).
// Ludvík expected that resolution to auto-float. Bumping both thresholds now
// catches:
//   - 1366×768       → width 1366 < 1400 triggers (classic laptop)
//   - 1920×1080 @125% zoom → effective 1536×864 → 864 < doesn't trigger, but
//                       1280×720 effective does via both
//   - 1280×720       → width 1280 < 1400 AND height 720 < 800 (both trigger)
//   - 1920×1080 @100% → 1920 ≥ 1400 AND 1080 ≥ 800 → docked (intended)
//
// Threshold rationale at 1400 px:
//   At 1400 px the 35% map column = 490 px. Tier table in §11 says full
//   severity legend appears at ≥520 px; at 490 we're in "compact" (legend
//   hidden, corner-legend still shown). That's the cutoff where the docked
//   map starts losing content. Below 1400 → float.
//
// Threshold rationale at 800 px:
//   The docked topbar floor at xl: is 380 px. 380/800 = 47.5% of viewport
//   already. 380/760 = 50% — the old threshold was right on the knife-edge.
//   Bumping to 800 catches 1366×768 and gives a margin so 125%-zoomed 720p
//   doesn't sit in the "barely docked" gap either.
const AUTO_FLOAT_MIN_WIDTH = 1400;
const AUTO_FLOAT_MIN_HEIGHT = 800;
const RESIZE_DEBOUNCE_MS = 150;

// Round 3: rendered-size auto-float thresholds.
//
// Viewport checks above catch the common "small display" case, but Ludvík hit
// a case where the viewport itself was fine (e.g. 1440×900) yet the docked
// video column rendered tiny because the topbar clamp (36vh..44vh) combined
// with the 65/35 grid squeezed #video-panel-body below readability. The
// viewport thresholds cannot detect this — only observing the actual laid-out
// box can.
//
// 420 px width: interact.js floor for *floating* video is 320 px, but docked
// video has pane chrome (header + padding) on top of the <video> element and
// native controls eating the bottom ~36 px, so 320 docked is unreadable.
// 420 leaves the actual video >= ~380 px wide after chrome, which matches
// the "usable" threshold on most laptops.
//
// 240 px height: at 16:9 a 420-wide video is ~236 tall; 240 is the matching
// floor. Below this the controls strip dominates the frame.
//
// Tuning note: if 420×240 turns out too aggressive (auto-floats when the user
// is fine with the cramped docked view) bump to 380×220. If it's not
// aggressive enough (stays docked when Ludvík thinks it should float) bump to
// 480×270 (matches the default floating size).
const AUTO_FLOAT_MIN_RENDERED_WIDTH = 420;
const AUTO_FLOAT_MIN_RENDERED_HEIGHT = 240;

let _videoOverlayInteractable = null;
// Guard set during active resize so the drag-end persist doesn't read
// transient element size (interact.js resize can fire during a drag if the
// pointer is inside the edge-resize zone). Also used to suppress the
// subsequent drag-end persist when a resize just occurred.
let _isResizing = false;
// Round 3: ResizeObserver watches the docked video body so auto-float fires
// when the rendered column becomes too narrow even though the viewport is
// roomy enough to pass _shouldAutoFloat()'s window.inner* checks. Connected
// only while docked; disconnected while floating (there's no docked body to
// watch, and watching the float slot would create a positive-feedback loop
// where shrinking the float keeps toggling mode).
let _dockedResizeObserver = null;

function _readOverlayState() {
  try {
    const raw = localStorage.getItem(VIDEO_OVERLAY_STORAGE_KEY);
    if (!raw) return { ...VIDEO_OVERLAY_DEFAULTS, _fresh: true };
    const parsed = JSON.parse(raw);
    // Additive migration: older records without activeTab get the default
    // ('video'). No schema version bump — all fields stay backward-compatible.
    return { ...VIDEO_OVERLAY_DEFAULTS, ...parsed };
  } catch (_) {
    return { ...VIDEO_OVERLAY_DEFAULTS, _fresh: true };
  }
}

/**
 * Read stored geometry but force mode=docked.
 *
 * Rationale (Round 1.7): a page refresh while floating was auto-restoring the
 * floating layout, which hides #video-dock-slot and leaves the topbar in a
 * single-column state with ~1400 px of empty space on either side of the
 * centered track map. Floating is an intentional user action; a cold load
 * should always start docked. Geometry (x/y/width/height) and activeTab are
 * still remembered so clicking Float brings the overlay back at its last
 * position/size/tab.
 */
function _readOverlayStateForInit() {
  const state = _readOverlayState();
  state.mode = 'docked';
  return state;
}

function _writeOverlayState(state) {
  try {
    localStorage.setItem(VIDEO_OVERLAY_STORAGE_KEY, JSON.stringify(state));
  } catch (_) {
    /* localStorage may be disabled / partitioned — non-fatal. */
  }
}

/**
 * Round 2.1: `userChoseMode` is a **session-scoped** flag — it does not
 * persist across page reloads. Once the user clicks Float or Dock during the
 * current page load, auto-float is frozen for that session; a refresh clears
 * the flag and restores viewport-driven auto-float behaviour.
 *
 * Rationale: the Round 2.0 localStorage-backed flag was too sticky. A single
 * click on Float while exploring was enough to silently disable auto-float
 * forever on this origin — Ludvík had to clear localStorage manually to get
 * the feature back. Matching his mental model: "I refreshed, I expect the
 * smart default to apply again."
 *
 * The legacy key is cleaned up on init (see `initVideoOverlay`) so stale
 * `'true'` values from prior sessions don't linger.
 */
let _userChoseModeSession = false;

function _readUserChoseMode() {
  return _userChoseModeSession;
}

function _writeUserChoseMode(value) {
  _userChoseModeSession = !!value;
}

/**
 * Measure the currently-docked video pane's body. Returns null when not
 * docked (the element is still in the DOM but its on-screen size is 0 x 0
 * or reflects the floating container — either way not meaningful for the
 * "is the docked column too cramped" question).
 *
 * Returns {width, height} from getBoundingClientRect() so sub-pixel values
 * are preserved; callers compare against integer thresholds so rounding is
 * irrelevant.
 */
function _getDockedVideoSize() {
  if (document.body.dataset.videoMode !== 'docked') return null;
  const body = document.getElementById('video-panel-body');
  if (!body) return null;
  const rect = body.getBoundingClientRect();
  // Element laid out but still 0×0 happens during a very brief window
  // between DOMContentLoaded and the first style flush. Treat as "not yet
  // measurable" so we don't auto-float on a spurious zero.
  if (rect.width === 0 && rect.height === 0) return null;
  return { width: rect.width, height: rect.height };
}

/**
 * Viewport or docked rendered size is too cramped to usefully dock the combined unit.
 *
 * "Cramped" means any of:
 *   - Width < 1400: 35% map column drops below ~490 px (below §11 "full" tier
 *     at 520 px — docked map starts hiding the severity legend).
 *   - Height < 800: docked topbar consumes ~50% of usable viewport height,
 *     squeezing the charts below. Also catches 1366×768 laptops.
 *   - Docked video body rendered < 420×240 (Round 3): topbar clamp +
 *     65/35 split can squeeze the video column below readability even
 *     though window.inner* passes both thresholds above. The rendered-size
 *     check is only meaningful while docked; when floating we rely on the
 *     viewport checks and interact.js's own min size.
 *
 * When true AND the user hasn't made an explicit mode choice, we auto-float.
 */
function _shouldAutoFloat() {
  const w = window.innerWidth || document.documentElement.clientWidth || 0;
  const h = window.innerHeight || document.documentElement.clientHeight || 0;
  if (w < AUTO_FLOAT_MIN_WIDTH || h < AUTO_FLOAT_MIN_HEIGHT) return true;
  const rendered = _getDockedVideoSize();
  if (rendered &&
      (rendered.width < AUTO_FLOAT_MIN_RENDERED_WIDTH ||
       rendered.height < AUTO_FLOAT_MIN_RENDERED_HEIGHT)) {
    return true;
  }
  return false;
}

function _getFloatSlot() {
  return document.getElementById('video-float-slot');
}

function _getDockSlot() {
  return document.getElementById('video-dock-slot');
}

/**
 * The element that gets reparented between dock and float slots. Despite
 * the historical `toggleVideoFloat` name, this is the combined video + map
 * panel — not just the video pane.
 */
function _getCombinedPanel() {
  return document.getElementById('combined-panel');
}

/**
 * Clamp the floating slot's position/size into the current viewport so
 * window shrinkage doesn't orphan the overlay off-screen.
 */
function _clampToViewport(x, y, width, height) {
  const vw = window.innerWidth || document.documentElement.clientWidth;
  const vh = window.innerHeight || document.documentElement.clientHeight;
  width = Math.min(Math.max(width, MIN_VIDEO_WIDTH), vw);
  height = Math.min(Math.max(height, MIN_VIDEO_HEIGHT), vh);
  x = Math.min(Math.max(x, 0), Math.max(0, vw - width));
  y = Math.min(Math.max(y, 0), Math.max(0, vh - height));
  return { x, y, width, height };
}

function _applyFloatGeometry(slot, state) {
  const clamped = _clampToViewport(state.x, state.y, state.width, state.height);
  slot.style.width = clamped.width + 'px';
  slot.style.height = clamped.height + 'px';
  slot.style.top = '0px';
  slot.style.left = '0px';
  slot.style.right = 'auto';
  slot.style.transform = `translate(${clamped.x}px, ${clamped.y}px)`;
  slot.dataset.x = String(clamped.x);
  slot.dataset.y = String(clamped.y);
  return clamped;
}

/**
 * Compute a sensible first-float position: top-right corner, default size.
 * Used when no localStorage state exists (first-ever float).
 *
 * Nitpicker R1: when auto-float fires at ~1100×768 the topbar placeholder
 * strip ("Video + Map floating" + Dock button) occupies the top ~40 px of
 * the viewport. The previous default y=16 put the floating panel's header
 * directly on top of that strip. Read the placeholder's actual rendered
 * height + 8 px breathing room; fall back to 52 px (≈40+8 with a margin)
 * if the placeholder isn't laid out yet at the moment we're called.
 */
function _defaultFloatGeometry() {
  const vw = window.innerWidth || document.documentElement.clientWidth || 1280;
  const width = Math.min(VIDEO_OVERLAY_DEFAULTS.width, Math.max(MIN_VIDEO_WIDTH, vw - 32));
  const height = Math.round((width * 9) / 16);
  const ph = document.getElementById('topbar-placeholder');
  // Placeholder is display:none in docked mode, so offsetHeight reads 0.
  // We still fall through to the 52 px default in that case.
  const phHeight = ph && ph.offsetHeight > 0 ? ph.offsetHeight : 0;
  const y = phHeight > 0 ? phHeight + 8 : 52;
  return {
    x: Math.max(16, vw - width - 16),
    y,
    width,
    height,
  };
}

function _setBodyMode(mode) {
  document.body.dataset.videoMode = mode;
}

function _currentActiveTab() {
  const panel = _getCombinedPanel();
  if (!panel) return 'video';
  const t = panel.dataset.activeTab;
  return t === 'map' ? 'map' : 'video';
}

function _persist() {
  const slot = _getFloatSlot();
  // Re-load from storage but strip the internal _fresh sentinel so it
  // never gets written back into localStorage.
  const state = _readOverlayState();
  delete state._fresh;
  const mode = document.body.dataset.videoMode === 'floating' ? 'floating' : 'docked';
  state.mode = mode;
  state.activeTab = _currentActiveTab();
  if (slot && mode === 'floating') {
    state.x = parseFloat(slot.dataset.x || '0') || 0;
    state.y = parseFloat(slot.dataset.y || '0') || 0;
    // Read width/height from inline style (set by _applyFloatGeometry or the
    // resize handler) rather than offsetWidth — offsetWidth can briefly
    // reflect transient browser layout during a drag gesture on some engines.
    const styledW = parseFloat(slot.style.width);
    const styledH = parseFloat(slot.style.height);
    if (!_isResizing) {
      state.width = Number.isFinite(styledW) && styledW > 0 ? styledW : state.width;
      state.height = Number.isFinite(styledH) && styledH > 0 ? styledH : state.height;
    }
  }
  _writeOverlayState(state);
}

/**
 * Round 3 fix: clear/restore the map panel's collapse state on float/dock.
 *
 * Problem: `#topbar-map` has `data-collapsible`; `toggleTopbarPanel()`
 * (app.js) sets `data-collapsed="true"` and the CSS rule at
 * `styles.css:289` unconditionally hides `[data-collapsible-body]` on any
 * collapsed ancestor — regardless of `body[data-video-mode]`. If the user
 * collapsed the map in docked mode and then floated, the Map tab body
 * stayed hidden and the floating window rendered an empty pane.
 *
 * On float: force-uncollapse the map (write `data-collapsed='false'`,
 * reset the `-/+` button label, disable the button — collapse is
 * meaningless when the tab switcher is the visibility driver). The fact
 * that we just clobbered the user's prior collapse choice is acceptable
 * because the alternative (honouring collapse in float) yields a blank
 * tab, which is strictly worse UX.
 *
 * On dock: re-enable the button. We deliberately do NOT re-apply the
 * previous collapsed state — a user who floats, tabs around, and docks
 * gets the map expanded, which matches "dock = show me everything" intent.
 */
function _forceMapExpandForFloat() {
  const mapPanel = document.getElementById('topbar-map');
  if (mapPanel && mapPanel.dataset.collapsed === 'true') {
    mapPanel.dataset.collapsed = 'false';
  }
  const collapseBtn = document.getElementById('btn-collapse-map');
  if (collapseBtn) {
    collapseBtn.textContent = '-';
    collapseBtn.disabled = true;
    collapseBtn.title = 'Collapse (disabled while floating — tabs control visibility)';
  }
}

function _restoreMapCollapseOnDock() {
  const collapseBtn = document.getElementById('btn-collapse-map');
  if (collapseBtn) {
    collapseBtn.disabled = false;
    collapseBtn.title = 'Collapse';
  }
}

/**
 * Move #track-readout between the docked video header and the floating
 * combined-panel header so the position text is always visible regardless
 * of mode.
 *
 * Docked: #track-readout lives inside the left cluster of #video-panel-head
 *   (inline next to the "Video" label). #video-panel-head is visible in
 *   docked mode, so the readout is naturally on-screen.
 *
 * Floating: #video-panel-head is display:none'd by CSS. Move #track-readout
 *   into #combined-panel-head (before the tab switcher) so it stays visible
 *   while floating.
 *
 * sync.js:updateReadout() targets only #readout-pos-text by ID, so
 * reparenting the outer #track-readout span is transparent to it.
 */
function _relocateTrackReadout(mode) {
  const readout = document.getElementById('track-readout');
  if (!readout) return;
  if (mode === 'floating') {
    const head = document.getElementById('combined-panel-head');
    if (!head) return;
    // Insert before the tab switcher so the readout sits at the far left.
    const tabs = document.getElementById('combined-tabs');
    if (tabs && tabs.parentNode === head) {
      if (readout.parentNode !== head) head.insertBefore(readout, tabs);
    } else if (readout.parentNode !== head) {
      head.insertBefore(readout, head.firstChild);
    }
  } else {
    // Restore to the left cluster inside #video-panel-head, after the
    // "Video" label div (which is its firstElementChild).
    const videoHead = document.getElementById('video-panel-head');
    if (!videoHead) return;
    const leftCluster = videoHead.firstElementChild;
    if (leftCluster && readout.parentNode !== leftCluster) {
      leftCluster.appendChild(readout);
    }
  }
}

/**
 * Relocate the video-controls element between the per-pane header
 * (#video-panel-head) in docked mode and the combined header
 * (#combined-panel-head) in floating mode. Controls need to stay reachable
 * on both tabs — when Map is active in floating mode, the video pane's own
 * header is display:none'd. Relocating guarantees lap-select + speed +
 * status remain visible. app.js only targets controls by ID, so this is safe.
 */
function _relocateVideoControls(mode) {
  const controls = document.getElementById('video-controls');
  if (!controls) return;
  if (mode === 'floating') {
    const head = document.getElementById('combined-panel-head');
    if (!head) return;
    // Use the named wrapper as the insertion anchor. Nitpicker R1 called
    // out that the old `dockBtn.parentNode` lookup depended on the Dock
    // button being wrapped in exactly one div that is a direct child of
    // head — fragile across future markup edits. `#combined-panel-head-right`
    // is a stable, self-documenting target.
    const rightCluster = document.getElementById('combined-panel-head-right');
    if (rightCluster && rightCluster.parentNode === head) {
      if (controls.parentNode !== head) head.insertBefore(controls, rightCluster);
    } else if (controls.parentNode !== head) {
      head.appendChild(controls);
    }
  } else {
    const videoHead = document.getElementById('video-panel-head');
    if (videoHead && controls.parentNode !== videoHead) {
      // Restore before #btn-video-float so visual order matches the original.
      const floatBtn = document.getElementById('btn-video-float');
      const rightCluster = floatBtn ? floatBtn.parentNode : null;
      if (rightCluster && rightCluster.parentNode === videoHead) {
        rightCluster.insertBefore(controls, floatBtn);
      } else {
        videoHead.appendChild(controls);
      }
    }
  }
}

function _floatCombined(storedState) {
  const panel = _getCombinedPanel();
  const floatSlot = _getFloatSlot();
  if (!panel || !floatSlot) return;

  // Capture playback state so reparenting doesn't lose it on any oddball browser.
  const video = document.getElementById('video-player');
  const wasPlaying = video && !video.paused && !video.ended;
  const currentTime = video ? video.currentTime : 0;

  // Disconnect BEFORE reparenting so the ResizeObserver doesn't fire one more
  // time on the docked body as it leaves the layout flow — that stray event
  // could re-enter _handleResize during a dock→float transition.
  _disconnectDockedResizeObserver();

  floatSlot.appendChild(panel);
  floatSlot.classList.remove('hidden');

  const geom = storedState && storedState.width ? storedState : _defaultFloatGeometry();
  const clamped = _applyFloatGeometry(floatSlot, geom);

  _setBodyMode('floating');
  _relocateVideoControls('floating');
  // Round 3 fix 1: force-expand the map before the Map tab might render, so
  // a previously-collapsed #topbar-map doesn't present a blank body.
  _forceMapExpandForFloat();
  // Round 3 fix 2: move the track-position readout inside the floating
  // window so it's paired with the Map and vacates the main scroll area.
  _relocateTrackReadout('floating');

  // Apply persisted active tab (default: 'video').
  const tab = storedState && storedState.activeTab === 'map' ? 'map' : 'video';
  panel.dataset.activeTab = tab;

  const btn = document.getElementById('btn-video-float');
  if (btn) {
    btn.textContent = 'Dock';
    btn.title = 'Dock video back into top bar';
  }

  // Restore playback if browser paused the element on reparenting.
  if (video) {
    try {
      if (Math.abs(video.currentTime - currentTime) > 0.05) {
        video.currentTime = currentTime;
      }
      if (wasPlaying && video.paused) {
        video.play().catch(() => {});
      }
    } catch (_) {
      /* best-effort */
    }
  }

  // Plotly: force a reflow so the map tab (if active) paints at the floating
  // window's size. Safe to call even when map is hidden — Plotly is idempotent.
  _resizeTrackMap();

  _persist();
  void clamped; // reference used only for the side effects above
}

function _dockCombined() {
  const panel = _getCombinedPanel();
  const dockSlot = _getDockSlot();
  const floatSlot = _getFloatSlot();
  if (!panel || !dockSlot || !floatSlot) return;

  const video = document.getElementById('video-player');
  const wasPlaying = video && !video.paused && !video.ended;
  const currentTime = video ? video.currentTime : 0;

  dockSlot.appendChild(panel);
  floatSlot.classList.add('hidden');

  _setBodyMode('docked');
  _relocateVideoControls('docked');
  // Round 3 fix 1: re-enable the map collapse button now that tabs no
  // longer govern visibility. Do NOT re-apply prior collapsed state —
  // "dock = show me everything" matches Ludvík's stated expectation.
  _restoreMapCollapseOnDock();
  // Round 3 fix 2: return the readout strip to its sticky docked home.
  _relocateTrackReadout('docked');
  // Connect the observer AFTER setting mode=docked so the first callback
  // sees the correct body.dataset.videoMode guard.
  _connectDockedResizeObserver();
  // Docked mode preserves activeTab in storage for the next float, but the
  // attribute itself doesn't matter because the CSS rules key off
  // body[data-video-mode='docked'] (both panes visible regardless).

  const btn = document.getElementById('btn-video-float');
  if (btn) {
    btn.textContent = 'Float';
    btn.title = 'Float video';
  }

  if (video) {
    try {
      if (Math.abs(video.currentTime - currentTime) > 0.05) {
        video.currentTime = currentTime;
      }
      if (wasPlaying && video.paused) {
        video.play().catch(() => {});
      }
    } catch (_) {
      /* best-effort */
    }
  }

  // Plotly reflows into the 35% column — call relayout defensively for
  // belt-and-suspenders even though `responsive: true` usually catches it.
  _resizeTrackMap();

  _persist();
}

/**
 * Idempotent Plotly reflow for the track map. Called on tab switch to map
 * and on dock/float transitions. Safe to call when #track-map hasn't been
 * initialized yet (Plotly.Plots.resize is a no-op on an empty div).
 *
 * Round 3 fix-up: browser Ctrl+/- zoom fires `visualViewport.resize` BEFORE
 * the browser has recomputed the CSS layout (flex/grid children + `vh`-based
 * topbar clamps). Calling Plotly.Plots.resize synchronously at that point
 * reads the stale clientWidth/clientHeight and the SVG keeps its pre-zoom
 * dimensions. Deferring to the next animation frame lets the browser settle
 * the new layout first, so Plotly reads the correct container size. The
 * Plotly.relayout({ autosize: true, width/height: null }) pass clears any
 * stale inline dimensions Plotly may have written during the last newPlot,
 * forcing it to re-derive size from the container on the subsequent resize
 * call.
 *
 * Skips entirely when #track-map is display:none (clientWidth=0) — that
 * happens when the Map tab is not active in floating mode, and a later
 * setCombinedTab('map') will trigger this same function anyway via its own
 * rAF hop.
 */
function _resizeTrackMap() {
  const div = document.getElementById('track-map');
  if (!div) return;
  if (typeof Plotly === 'undefined' || !Plotly.Plots) return;
  // `data` is set only after Plotly.newPlot has run. Skip if empty to avoid
  // a noisy console error.
  if (!div.data) return;
  requestAnimationFrame(() => {
    // Re-check after rAF — the div may have been removed/hidden between the
    // scheduling and the callback (rare, but defensive).
    if (!div.isConnected || !div.data) return;
    if (div.clientWidth === 0 || div.clientHeight === 0) return;
    try {
      Plotly.relayout(div, { autosize: true, width: null, height: null });
      Plotly.Plots.resize(div);
    } catch (_) {
      /* non-fatal */
    }
  });
}

/**
 * Public tab-switch handler, wired from the inline onclick= on the tab
 * buttons. Writes the attribute on #combined-panel, persists activeTab,
 * and forces a Plotly reflow when switching to map.
 *
 * Idempotent: clicking the already-active tab is a no-op apart from a
 * redundant localStorage write.
 */
function setCombinedTab(tab) {
  const name = tab === 'map' ? 'map' : 'video';
  const panel = _getCombinedPanel();
  if (!panel) return;
  panel.dataset.activeTab = name;
  if (name === 'map') {
    // Round 3 belt-and-suspenders: if anything has left #topbar-map
    // collapsed (future regression, stray toggle while floating, weird
    // state from a reload mid-float) force-expand so the Map tab body
    // cannot render blank. _floatCombined already did this on the mode
    // switch; this is a second line of defense for the tab-switch path.
    const mapPanel = document.getElementById('topbar-map');
    if (mapPanel && mapPanel.dataset.collapsed === 'true') {
      mapPanel.dataset.collapsed = 'false';
    }
    // Use rAF to let the display:none → display:block swap land before
    // Plotly measures. Without this, Plotly can occasionally read a zero
    // width on the first switch after float.
    requestAnimationFrame(_resizeTrackMap);
  }
  _persist();
}

/**
 * Public entry point bound to the Float/Dock button via inline onclick.
 *
 * On first-ever float (no localStorage yet) we must use the top-right default
 * position — not the zeroed defaults baked into `VIDEO_OVERLAY_DEFAULTS`.
 * Detected via the `_fresh` sentinel set in `_readOverlayState()`.
 *
 * Round 2.1: any click here records the explicit user choice for the current
 * page session, disabling viewport-driven auto-float decisions until the user
 * reloads. The flag is session-scoped (not persisted) — see
 * `_userChoseModeSession` for the rationale.
 */
function toggleVideoFloat() {
  _writeUserChoseMode(true);
  const mode = document.body.dataset.videoMode === 'floating' ? 'floating' : 'docked';
  if (mode === 'floating') {
    _dockCombined();
  } else {
    const stored = _readOverlayState();
    const hasFloatedBefore = !stored._fresh && (stored.mode === 'floating' || stored.x > 0 || stored.y > 0);
    const geom = hasFloatedBefore ? stored : { ..._defaultFloatGeometry(), activeTab: stored.activeTab };
    _floatCombined(geom);
  }
}

/**
 * interact.js drag handler. Uses the data-x/data-y + transform pattern so
 * we don't incur layout thrash during drag.
 */
function _onDragMove(event) {
  const slot = event.target;
  const x = (parseFloat(slot.dataset.x) || 0) + event.dx;
  const y = (parseFloat(slot.dataset.y) || 0) + event.dy;
  slot.style.transform = `translate(${x}px, ${y}px)`;
  slot.dataset.x = String(x);
  slot.dataset.y = String(y);
}

/**
 * interact.js resize handler. interact.js feeds us the new rect; we apply
 * the size and correct the translate for edges that moved (top/left).
 */
function _onResizeStart() {
  _isResizing = true;
}

function _onResizeMove(event) {
  const slot = event.target;
  let x = parseFloat(slot.dataset.x) || 0;
  let y = parseFloat(slot.dataset.y) || 0;
  x += event.deltaRect.left;
  y += event.deltaRect.top;
  slot.style.width = event.rect.width + 'px';
  slot.style.height = event.rect.height + 'px';
  slot.style.transform = `translate(${x}px, ${y}px)`;
  slot.dataset.x = String(x);
  slot.dataset.y = String(y);
}

function _onResizeEnd() {
  _isResizing = false;
  _persist();
  // Force a Plotly reflow after resize ends — needed when the Map tab is
  // active and the floating window crosses one of the §11 tier thresholds.
  _resizeTrackMap();
}

function _initInteract() {
  if (_videoOverlayInteractable) return;
  if (typeof interact !== 'function') {
    console.warn('interact.js not loaded; floating video will not drag/resize');
    return;
  }
  const slot = _getFloatSlot();
  if (!slot) return;

  _videoOverlayInteractable = interact(slot)
    .draggable({
      // Drag handle is the combined-panel header (only visible when floating).
      // The per-pane #video-panel-head is display:none'd when floating, so
      // it's not a valid handle anymore.
      allowFrom: '#combined-panel-head',
      // Only treat it as a drag after a small displacement; this keeps a
      // click/tap on the resize edges from being racily picked up as a drag.
      startAxis: 'xy',
      lockAxis: 'xy',
      inertia: false,
      listeners: {
        move: _onDragMove,
        end: _persist,
      },
      modifiers: [
        interact.modifiers.restrictRect({
          restriction: 'parent',
          endOnly: false,
        }),
        interact.modifiers.snap({
          targets: [interact.snappers.grid({ x: SNAP_GRID, y: SNAP_GRID })],
          range: Infinity,
          relativePoints: [{ x: 0, y: 0 }],
        }),
      ],
    })
    // NOTE: top edge deliberately disabled. The header (`#combined-panel-head`)
    // sits at the top of the slot and is the drag handle; enabling top-edge
    // resize made a drag-from-header racily co-activate a resize, which
    // snapped the panel to the 320x180 minimum. User can still resize via
    // left/right/bottom edges + bottom-left / bottom-right corners.
    .resizable({
      edges: { top: false, left: true, bottom: true, right: true },
      inertia: false,
      listeners: {
        start: _onResizeStart,
        move: _onResizeMove,
        end: _onResizeEnd,
      },
      modifiers: [
        interact.modifiers.aspectRatio({
          ratio: 16 / 9,
          equalDelta: false,
        }),
        interact.modifiers.restrictSize({
          min: { width: MIN_VIDEO_WIDTH, height: MIN_VIDEO_HEIGHT },
        }),
        interact.modifiers.restrictEdges({
          outer: 'parent',
        }),
      ],
    });
}

/**
 * Round 3: connect a ResizeObserver to the docked video body so a shrinking
 * rendered column triggers auto-float even when window.inner* passes the
 * viewport thresholds.
 *
 * Lifecycle invariant: observer exists only while docked. Floating disconnects
 * it (see _disconnectDockedResizeObserver) because (a) the docked body is no
 * longer on-screen and (b) observing while floating would create a feedback
 * loop — the floating slot shrinking past its minimum would keep re-firing
 * "auto-float" decisions. We also debounce through the same RESIZE_DEBOUNCE_MS
 * path as window resize so one layout pass can't thrash the mode.
 */
function _connectDockedResizeObserver() {
  if (_dockedResizeObserver) return;
  if (typeof ResizeObserver !== 'function') return;
  const body = document.getElementById('video-panel-body');
  if (!body) return;
  _dockedResizeObserver = new ResizeObserver(() => {
    // Guard against fire-after-disconnect: if we've flipped to floating
    // between the browser scheduling the callback and this line running,
    // skip. The observer is disconnected synchronously on dock→float so
    // this is defensive.
    if (document.body.dataset.videoMode !== 'docked') return;
    if (_resizeDebounceTimer !== null) clearTimeout(_resizeDebounceTimer);
    _resizeDebounceTimer = setTimeout(_handleResize, RESIZE_DEBOUNCE_MS);
  });
  _dockedResizeObserver.observe(body);
}

function _disconnectDockedResizeObserver() {
  if (!_dockedResizeObserver) return;
  try {
    _dockedResizeObserver.disconnect();
  } catch (_) {
    /* non-fatal */
  }
  _dockedResizeObserver = null;
}

/**
 * Re-clamp the floating overlay on viewport resize so it never sits off-screen.
 * Cheap: if not floating we bail immediately.
 */
function _reclampFloating() {
  if (document.body.dataset.videoMode !== 'floating') return;
  const slot = _getFloatSlot();
  if (!slot) return;
  const state = {
    x: parseFloat(slot.dataset.x || '0') || 0,
    y: parseFloat(slot.dataset.y || '0') || 0,
    width: slot.offsetWidth,
    height: slot.offsetHeight,
  };
  _applyFloatGeometry(slot, state);
  _persist();
}

/**
 * Round 2.0: debounced resize handler. Two responsibilities:
 *   1. If floating, re-clamp into the new viewport so the overlay isn't
 *      orphaned off-screen (prior behaviour).
 *   2. If the user hasn't made an explicit mode choice, re-evaluate the
 *      auto-float heuristic and toggle mode live. This makes a user
 *      dragging a browser window onto a smaller/larger display see the
 *      "right" layout without a refresh.
 */
let _resizeDebounceTimer = null;
function _onWindowResize() {
  if (_resizeDebounceTimer !== null) clearTimeout(_resizeDebounceTimer);
  _resizeDebounceTimer = setTimeout(_handleResize, RESIZE_DEBOUNCE_MS);
}

function _handleResize() {
  _resizeDebounceTimer = null;
  const userChose = _readUserChoseMode();
  const currentMode = document.body.dataset.videoMode === 'floating' ? 'floating' : 'docked';

  if (!userChose) {
    const wantFloat = _shouldAutoFloat();
    if (wantFloat && currentMode === 'docked') {
      // Viewport just got cramped — auto-float. Use stored geometry if any,
      // else top-right default. Do NOT set userChoseMode; this is viewport-driven.
      //
      // Round 2.1 fix: align this guard with the one in toggleVideoFloat
      // (line ~536). The prior `!stored._fresh && width>0 && height>0` guard
      // was effectively always-true on reload — `_fresh` is an in-memory
      // sentinel that never gets persisted, so after initVideoOverlay writes
      // {mode:'docked', x:0, y:0, width:480, height:270} to localStorage,
      // `_handleResize` read width=480>0 and treated the zeroed x/y as a
      // valid float position. Result: auto-floated overlay landed at (0,0),
      // covering Sessions/Plot buttons. The corrected heuristic matches user
      // intent: we've floated before only if mode was floating OR we have a
      // non-zero position on record.
      const stored = _readOverlayState();
      const hasFloatedBefore = stored.mode === 'floating' || stored.x > 0 || stored.y > 0;
      const geom = hasFloatedBefore ? stored : { ..._defaultFloatGeometry(), activeTab: stored.activeTab };
      _floatCombined(geom);
      // _floatCombined() already calls _resizeTrackMap internally.
      return;
    }
    if (!wantFloat && currentMode === 'floating') {
      // Viewport just got roomy enough — auto-dock.
      _dockCombined();
      return;
    }
  }

  // Either user made an explicit choice, or mode already matches what we'd pick:
  // just re-clamp if floating (prior behaviour).
  _reclampFloating();

  // Nitpicker R1: browser zoom (Ctrl+ / Ctrl-) and legitimate viewport
  // changes fire a `resize` event but do NOT change the mode. In docked
  // mode neither _reclampFloating nor the auto-float branches call
  // _resizeTrackMap, so Plotly never reflows — the map shrinks into the
  // corner while the container grows. Always invoke Plotly.Plots.resize
  // at the tail of _handleResize; the call is cheap and idempotent (safely
  // returns when #track-map.data is undefined).
  _resizeTrackMap();
}

function initVideoOverlay() {
  // Round 2.1: scrub the legacy persisted `userChoseMode` key. Round 2.0
  // wrote `'true'` here on every Float/Dock click and read it back on load,
  // which froze auto-float across reloads. Users who ever clicked the button
  // got permanently locked out of the smart default. The key is now scoped
  // to the page session via `_userChoseModeSession`; this cleanup ensures
  // returning users aren't stuck behind a stale localStorage entry.
  try {
    localStorage.removeItem(VIDEO_OVERLAY_LEGACY_USER_CHOSE_KEY);
  } catch (_) {
    /* non-fatal */
  }

  _setBodyMode('docked');
  _initInteract();

  // Round 2.1: mode selection on cold load.
  //  - Geometry is always read from storage (preserves user's last float
  //    position/size across refreshes).
  //  - Mode selection: `_userChoseModeSession` is always `false` here (we
  //    just loaded), so auto-float is ALWAYS consulted on reload. Tiny or
  //    narrow viewport → start floating; otherwise force docked (per Round
  //    1.7 rationale: floating is intentional and a refresh should start
  //    predictably docked). The user's session-scoped choice only takes
  //    effect on subsequent resize events after they click Float/Dock.
  const stored = _readOverlayStateForInit();
  const userChose = _readUserChoseMode();
  const autoFloat = !userChose && _shouldAutoFloat();

  if (autoFloat) {
    // Mirror the geometry into storage with mode=floating so _persist reads
    // a coherent state. Note: we do NOT set userChoseMode here — a later
    // resize could still auto-dock if the viewport becomes roomy.
    const hasStoredGeom = stored.width > 0 && stored.height > 0 && !stored._fresh;
    const geom = hasStoredGeom ? stored : { ..._defaultFloatGeometry(), activeTab: stored.activeTab };
    _floatCombined(geom);
  } else {
    _writeOverlayState({
      mode: 'docked',
      x: stored.x,
      y: stored.y,
      width: stored.width,
      height: stored.height,
      activeTab: stored.activeTab,
    });
    // Round 3: we're starting docked, so connect the rendered-size observer.
    // Defer one frame so the initial CSS layout has settled — without this,
    // the very first observation on a cold load can report a transient size
    // (e.g. 0×0 or pre-clamp) that would falsely fire auto-float.
    requestAnimationFrame(() => {
      if (document.body.dataset.videoMode === 'docked') {
        _connectDockedResizeObserver();
        // Also re-evaluate once the layout is stable: on a cold load at a
        // viewport that passes the viewport thresholds but yields a cramped
        // rendered column, the ResizeObserver's first callback may never
        // fire (observation starts *after* initial layout). Run the
        // heuristic explicitly once here so Ludvík's "video was too small"
        // case still flips to float.
        if (!userChose && _shouldAutoFloat()) {
          const hasStoredGeom2 = stored.width > 0 && stored.height > 0 && !stored._fresh;
          const geom2 = hasStoredGeom2
            ? stored
            : { ..._defaultFloatGeometry(), activeTab: stored.activeTab };
          _floatCombined(geom2);
        }
      }
    });
  }

  window.addEventListener('resize', _onWindowResize);
  // Round 3 fix 3: Chrome/Chromium does NOT reliably fire `window.resize`
  // on Ctrl+/- browser zoom — the event actually lands on
  // `window.visualViewport.resize`. Without this listener the track map
  // stays at its pre-zoom pixel size because _resizeTrackMap is only
  // called at the tail of _handleResize. The existing 150 ms debounce in
  // _onWindowResize protects against the high-frequency fire on pinch
  // gestures, so we can safely reuse the same handler.
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', _onWindowResize);
  }
}

// Auto-init once DOM is ready.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVideoOverlay);
} else {
  initVideoOverlay();
}

// Expose globals used from index.html inline handlers.
window.toggleVideoFloat = toggleVideoFloat;
window.setCombinedTab = setCombinedTab;
