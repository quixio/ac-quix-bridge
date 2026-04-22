/**
 * Video overlay controller — dock/float toggle for the Video panel.
 *
 * The Video panel (`#video-panel`) lives between two slots:
 *   - `#video-dock-slot`  : the left cell of the top bar.
 *   - `#video-float-slot` : a `position:fixed` container that interact.js
 *                            drags + resizes. Hidden until the user floats.
 *
 * State is tracked on `<body data-video-mode="docked|floating">` and mirrored
 * to `localStorage['telemetryExplorer.videoOverlay.v1']` as:
 *   { mode, x, y, width, height }
 *
 * The actual `<video>` element is reparented via `appendChild`. Modern browsers
 * (Chromium, Firefox) preserve playback state across reparenting; the video
 * sync contract in `.claude/skills/video-seeking/SKILL.md` stays intact because
 * `videoState.element` still points at the same DOM node.
 *
 * Plain (non-module) script so `toggleVideoFloat` is globally accessible from
 * the inline onclick= in index.html.
 */

const VIDEO_OVERLAY_STORAGE_KEY = 'telemetryExplorer.videoOverlay.v1';
// Round 2.0: separate key tracks whether the user has ever explicitly clicked
// Float/Dock. Only then do we stop making viewport-driven mode decisions.
// Kept separate from the geometry key so clearing mode preference doesn't
// wipe the user's preferred floating position/size.
const VIDEO_OVERLAY_USER_CHOSE_KEY = 'telemetryExplorer.videoOverlay.userChoseMode';
const VIDEO_OVERLAY_DEFAULTS = {
  mode: 'docked',
  x: 0,
  y: 0,
  width: 480,
  height: 270, // 16:9 of 480
};
const MIN_VIDEO_WIDTH = 320;
const MIN_VIDEO_HEIGHT = 180;
const SNAP_GRID = 8;

// Round 2.0: viewport thresholds for "too cramped to dock usefully".
// - Width < 1024 → already below Tailwind `lg:` breakpoint, topbar is single
//   column and docking stacks video+map vertically, consuming the full
//   viewport height. Float is more useful.
// - Height < 700 → even at `lg:` width, the docked topbar's 36vh clamp leaves
//   < 450 px for the main content below (sessions, signals, charts). Floating
//   the video frees the whole topbar for the track map.
const AUTO_FLOAT_MIN_WIDTH = 1024;
const AUTO_FLOAT_MIN_HEIGHT = 700;
const RESIZE_DEBOUNCE_MS = 150;

let _videoOverlayInteractable = null;
// Guard set during active resize so the drag-end persist doesn't read
// transient element size (interact.js resize can fire during a drag if the
// pointer is inside the edge-resize zone). Also used to suppress the
// subsequent drag-end persist when a resize just occurred.
let _isResizing = false;

function _readOverlayState() {
  try {
    const raw = localStorage.getItem(VIDEO_OVERLAY_STORAGE_KEY);
    if (!raw) return { ...VIDEO_OVERLAY_DEFAULTS, _fresh: true };
    const parsed = JSON.parse(raw);
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
 * should always start docked. Geometry (x/y/width/height) is still remembered
 * so clicking Float brings the overlay back at its last position/size.
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
 * Round 2.0: has the user ever explicitly picked a mode on this origin?
 * Reads `telemetryExplorer.videoOverlay.userChoseMode`. Returns false for
 * unset/invalid/non-"true" values so a hostile or partitioned localStorage
 * defaults to "no choice made" (= honor viewport auto-float heuristic).
 */
function _readUserChoseMode() {
  try {
    return localStorage.getItem(VIDEO_OVERLAY_USER_CHOSE_KEY) === 'true';
  } catch (_) {
    return false;
  }
}

function _writeUserChoseMode(value) {
  try {
    localStorage.setItem(VIDEO_OVERLAY_USER_CHOSE_KEY, value ? 'true' : 'false');
  } catch (_) {
    /* non-fatal */
  }
}

/**
 * Round 2.0: viewport is too cramped to usefully dock the video.
 *
 * "Cramped" means either:
 *   - Width < 1024: below `lg:` Tailwind breakpoint; topbar already reflows
 *     to a single column, so docking produces a stacked video+map pile
 *     pushing main content far down.
 *   - Height < 700: even at `lg:` width, the docked topbar's 36vh clamp
 *     bottoms out at 320 px and swallows ~46% of the usable viewport.
 *     Floating the video gives the track map the full topbar and frees
 *     vertical space for charts below.
 *
 * When true AND the user hasn't made an explicit mode choice, we auto-float.
 */
function _shouldAutoFloat() {
  const w = window.innerWidth || document.documentElement.clientWidth || 0;
  const h = window.innerHeight || document.documentElement.clientHeight || 0;
  return w < AUTO_FLOAT_MIN_WIDTH || h < AUTO_FLOAT_MIN_HEIGHT;
}

function _getFloatSlot() {
  return document.getElementById('video-float-slot');
}

function _getDockSlot() {
  return document.getElementById('video-dock-slot');
}

function _getPanel() {
  return document.getElementById('video-panel');
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
 */
function _defaultFloatGeometry() {
  const vw = window.innerWidth || document.documentElement.clientWidth || 1280;
  const width = Math.min(VIDEO_OVERLAY_DEFAULTS.width, Math.max(MIN_VIDEO_WIDTH, vw - 32));
  const height = Math.round((width * 9) / 16);
  return {
    x: Math.max(16, vw - width - 16),
    y: 16,
    width,
    height,
  };
}

function _setBodyMode(mode) {
  document.body.dataset.videoMode = mode;
}

function _persist() {
  const slot = _getFloatSlot();
  // Re-load from storage but strip the internal _fresh sentinel so it
  // never gets written back into localStorage.
  const state = _readOverlayState();
  delete state._fresh;
  const mode = document.body.dataset.videoMode === 'floating' ? 'floating' : 'docked';
  state.mode = mode;
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

function _floatVideo(storedState) {
  const panel = _getPanel();
  const floatSlot = _getFloatSlot();
  if (!panel || !floatSlot) return;

  // Capture playback state so reparenting doesn't lose it on any oddball browser.
  const video = document.getElementById('video-player');
  const wasPlaying = video && !video.paused && !video.ended;
  const currentTime = video ? video.currentTime : 0;

  floatSlot.appendChild(panel);
  floatSlot.classList.remove('hidden');

  const geom = storedState && storedState.width ? storedState : _defaultFloatGeometry();
  const clamped = _applyFloatGeometry(floatSlot, geom);

  _setBodyMode('floating');

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

  _persist();
  void clamped; // reference used only for the side effects above
}

function _dockVideo() {
  const panel = _getPanel();
  const dockSlot = _getDockSlot();
  const floatSlot = _getFloatSlot();
  if (!panel || !dockSlot || !floatSlot) return;

  const video = document.getElementById('video-player');
  const wasPlaying = video && !video.paused && !video.ended;
  const currentTime = video ? video.currentTime : 0;

  dockSlot.appendChild(panel);
  floatSlot.classList.add('hidden');

  _setBodyMode('docked');

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

  _persist();
}

/**
 * Public entry point bound to the Float/Dock button via inline onclick.
 *
 * On first-ever float (no localStorage yet) we must use the top-right default
 * position — not the zeroed defaults baked into `VIDEO_OVERLAY_DEFAULTS`.
 * Detected via the `_fresh` sentinel set in `_readOverlayState()`.
 *
 * Round 2.0: any click here records the explicit user choice, which disables
 * future viewport-driven auto-float decisions on this origin until localStorage
 * is cleared.
 */
function toggleVideoFloat() {
  _writeUserChoseMode(true);
  const mode = document.body.dataset.videoMode === 'floating' ? 'floating' : 'docked';
  if (mode === 'floating') {
    _dockVideo();
  } else {
    const stored = _readOverlayState();
    const hasFloatedBefore = !stored._fresh && (stored.mode === 'floating' || stored.x > 0 || stored.y > 0);
    const geom = hasFloatedBefore ? stored : _defaultFloatGeometry();
    _floatVideo(geom);
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
      allowFrom: '#video-panel-head',
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
    // NOTE: top edge deliberately disabled. The header (`#video-panel-head`)
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
      const stored = _readOverlayState();
      const hasFloatedBefore = !stored._fresh && (stored.width > 0 && stored.height > 0);
      const geom = hasFloatedBefore ? stored : _defaultFloatGeometry();
      _floatVideo(geom);
      return;
    }
    if (!wantFloat && currentMode === 'floating') {
      // Viewport just got roomy enough — auto-dock.
      _dockVideo();
      return;
    }
  }

  // Either user made an explicit choice, or mode already matches what we'd pick:
  // just re-clamp if floating (prior behaviour).
  _reclampFloating();
}

function initVideoOverlay() {
  _setBodyMode('docked');
  _initInteract();

  // Round 2.0: mode selection on cold load.
  //  - Geometry is always read from storage (preserves user's last float
  //    position/size across refreshes).
  //  - Mode selection:
  //      * If user has NOT explicitly chosen Float/Dock before (tracked via
  //        `userChoseMode` key), consult `_shouldAutoFloat()` against the
  //        current viewport. Tiny/narrow viewport → start floating.
  //      * Otherwise, force docked (per Round 1.7 rationale: floating is an
  //        intentional action, and a refresh should start predictably docked).
  const stored = _readOverlayStateForInit();
  const userChose = _readUserChoseMode();
  const autoFloat = !userChose && _shouldAutoFloat();

  if (autoFloat) {
    // Mirror the geometry into storage with mode=floating so _persist reads
    // a coherent state. Note: we do NOT set userChoseMode here — a later
    // resize could still auto-dock if the viewport becomes roomy.
    const hasStoredGeom = stored.width > 0 && stored.height > 0 && !stored._fresh;
    const geom = hasStoredGeom ? stored : _defaultFloatGeometry();
    _floatVideo(geom);
  } else {
    _writeOverlayState({
      mode: 'docked',
      x: stored.x,
      y: stored.y,
      width: stored.width,
      height: stored.height,
    });
  }

  window.addEventListener('resize', _onWindowResize);
}

// Auto-init once DOM is ready.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVideoOverlay);
} else {
  initVideoOverlay();
}

// Expose globals used from index.html inline handlers.
window.toggleVideoFloat = toggleVideoFloat;
