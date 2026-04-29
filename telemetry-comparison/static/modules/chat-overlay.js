/**
 * AI chat panel — supports two modes:
 *   - 'floating': fixed overlay, drag/resize via interact.js (slice 1).
 *   - 'docked':   reparented into <aside id="chat-dock-slot"> as a
 *                 fixed-width right sidebar next to <main>. Topbar untouched.
 *
 * Mode is session-scoped and viewport-driven (not persisted): on load we
 * pick docked when the viewport is wide enough, else floating. An explicit
 * user choice via the in-header Dock/Float button overrides the auto pick
 * for the rest of the page session, mirroring video-overlay.js.
 *
 * Geometry of the floating slot is persisted in localStorage under
 * `telemetryExplorer.chatPanel.v1`. `mode` is intentionally not persisted —
 * we recompute it from viewport on every load.
 *
 * Public surface (called from app.js init):
 *   initChatOverlay()
 */

const STORAGE_KEY = 'telemetryExplorer.chatPanel.v1';

const DEFAULTS = {
  visible: false,
  x: -1,
  y: -1,
  width: 400,
  height: 650,
};

const MIN_WIDTH = 280;
const MIN_HEIGHT = 320;
const VIEWPORT_MARGIN = 16;

/** Auto-dock thresholds. Below either dimension we stay floating, and the
 *  Dock toggle button is hidden so the user can't pick docked on a screen
 *  that has no room. Picked to dock 14"+ MacBooks while keeping iPads and
 *  13" laptops floating. */
const AUTO_DOCK_MIN_WIDTH = 1440;
const AUTO_DOCK_MIN_HEIGHT = 800;

/** Session-scoped: if the user clicks the Dock/Float toggle we set this and
 *  stop auto-switching on viewport changes. Reset every page load. */
let _userChoseModeSession = false;

function _readState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS, _firstVisit: true };
    return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch (_) {
    return { ...DEFAULTS, _firstVisit: true };
  }
}

function _persist(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (_) {
    /* localStorage may be partitioned/disabled — non-fatal. */
  }
}

function _viewportAllowsDock() {
  const w = window.innerWidth || document.documentElement.clientWidth || 0;
  const h = window.innerHeight || document.documentElement.clientHeight || 0;
  return w >= AUTO_DOCK_MIN_WIDTH && h >= AUTO_DOCK_MIN_HEIGHT;
}

function _currentMode() {
  return document.body.dataset.chatMode === 'docked' ? 'docked' : 'floating';
}

function _setMode(mode) {
  document.body.dataset.chatMode = mode;
  const btn = document.getElementById('chat-mode-toggle');
  if (btn) {
    btn.textContent = mode === 'docked' ? 'Float' : 'Dock';
    btn.title = mode === 'docked' ? 'Float' : 'Dock';
    // Hide the toggle when the viewport is too narrow to dock — only relevant
    // when currently floating; if we're already docked, leave it visible so
    // the user can always escape back to floating.
    if (mode === 'floating' && !_viewportAllowsDock()) {
      btn.style.display = 'none';
    } else {
      btn.style.display = '';
    }
  }
}

/** Bottom edge of the topbar in viewport coords. The floating chat must
 *  not overlap the video+map, so this is the minimum y allowed. Falls back
 *  to 0 if the topbar is missing (e.g. unit tests). */
function _topbarBottom() {
  const topbar = document.getElementById('topbar');
  if (!topbar) return 0;
  const rect = topbar.getBoundingClientRect();
  return Math.max(0, rect.bottom);
}

function _defaultGeometry() {
  const vw = window.innerWidth || document.documentElement.clientWidth || 1280;
  const vh = window.innerHeight || document.documentElement.clientHeight || 720;
  const top = _topbarBottom() + VIEWPORT_MARGIN;
  const maxHeight = Math.max(MIN_HEIGHT, vh - top - VIEWPORT_MARGIN);
  const width = Math.min(DEFAULTS.width, Math.max(MIN_WIDTH, vw - 2 * VIEWPORT_MARGIN));
  // Default: full available height (top of topbar+margin → bottom-margin),
  // pinned to the right edge. Same idiom as the docked sidebar so toggling
  // dock↔float doesn't change the visible footprint dramatically.
  const height = Math.min(maxHeight, Math.max(MIN_HEIGHT, maxHeight));
  return {
    x: Math.max(VIEWPORT_MARGIN, vw - width - VIEWPORT_MARGIN),
    y: top,
    width,
    height,
  };
}

function _clamp(geom) {
  // Floating chat is free — user can drag/resize anywhere inside the
  // viewport, including over the video+map topbar. The topbar-bottom
  // floor only applies to the *default* geometry so a freshly opened
  // panel doesn't visually overlap the topbar; subsequent moves are
  // unconstrained beyond the viewport edges.
  const vw = window.innerWidth || document.documentElement.clientWidth;
  const vh = window.innerHeight || document.documentElement.clientHeight;
  const width = Math.max(MIN_WIDTH, Math.min(geom.width, vw - 2 * VIEWPORT_MARGIN));
  const height = Math.max(MIN_HEIGHT, Math.min(geom.height, vh - 2 * VIEWPORT_MARGIN));
  const x = Math.max(0, Math.min(geom.x, vw - width));
  const y = Math.max(0, Math.min(geom.y, vh - height));
  return { x, y, width, height };
}

function _applyGeometry(slot, geom) {
  const c = _clamp(geom);
  slot.style.transform = `translate(${c.x}px, ${c.y}px)`;
  slot.style.width = `${c.width}px`;
  slot.style.height = `${c.height}px`;
  slot.dataset.x = String(c.x);
  slot.dataset.y = String(c.y);
  return c;
}

function _focusInput() {
  const input = document.getElementById('chat-input');
  if (input) input.focus();
}

function _getPanel() {
  return document.getElementById('chat-panel');
}

function _getFloatSlot() {
  return document.getElementById('chat-float-slot');
}

function _getDockSlot() {
  return document.getElementById('chat-dock-slot');
}

/** Move #chat-panel into the dock slot. Floating slot's inline geometry is
 *  preserved (we only change the parent). Returns true on success. */
function _reparentToDock() {
  const panel = _getPanel();
  const dock = _getDockSlot();
  const float = _getFloatSlot();
  if (!panel || !dock || !float) return false;
  if (panel.parentElement !== dock) {
    dock.appendChild(panel);
  }
  dock.classList.remove('hidden');
  // The float slot's transform/size is irrelevant while docked; just hide it
  // so it doesn't intercept clicks via its 8 px halo. Geometry stays in
  // dataset so re-floating restores the previous position.
  float.classList.add('hidden');
  return true;
}

/** Move #chat-panel back into the floating slot and reapply geometry. */
function _reparentToFloat() {
  const panel = _getPanel();
  const dock = _getDockSlot();
  const float = _getFloatSlot();
  if (!panel || !dock || !float) return false;
  if (panel.parentElement !== float) {
    float.appendChild(panel);
  }
  dock.classList.add('hidden');
  const state = _readState();
  const hasStored = state.x >= 0 && state.y >= 0;
  const geom = hasStored
    ? { x: state.x, y: state.y, width: state.width, height: state.height }
    : _defaultGeometry();
  _applyGeometry(float, geom);
  float.classList.remove('hidden');
  return true;
}

function _show(state) {
  // In docked mode the panel lives in the sidebar — no geometry to apply,
  // and the entrance animation is a width fade rather than the floating
  // slide-up. We still flip the toggle's aria-expanded for both modes.
  const toggle = document.getElementById('chat-toggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'true');

  if (_currentMode() === 'docked') {
    _reparentToDock();
    _updateToggleVisibility();
    requestAnimationFrame(() => _focusInput());
    return;
  }

  const slot = _getFloatSlot();
  if (!slot) return;
  const hasStored = state.x >= 0 && state.y >= 0;
  const geom = hasStored
    ? { x: state.x, y: state.y, width: state.width, height: state.height }
    : _defaultGeometry();
  _applyGeometry(slot, geom);
  if (_hideTimer !== null) {
    clearTimeout(_hideTimer);
    _hideTimer = null;
  }
  slot.classList.remove('leaving');
  slot.classList.add('entering');
  slot.classList.remove('hidden');
  _updateToggleVisibility();
  requestAnimationFrame(() => {
    slot.classList.remove('entering');
    _focusInput();
  });
}

const HIDE_TRANSITION_MS = 220;
let _hideTimer = null;

function _hide() {
  const toggle = document.getElementById('chat-toggle');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');

  if (_currentMode() === 'docked') {
    const dock = _getDockSlot();
    if (dock) dock.classList.add('hidden');
    _updateToggleVisibility();
    return;
  }

  const slot = _getFloatSlot();
  if (!slot) return;
  if (slot.classList.contains('hidden')) return;

  const reduceMotion =
    window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (reduceMotion) {
    slot.classList.add('hidden');
    _updateToggleVisibility();
    return;
  }

  if (_hideTimer !== null) clearTimeout(_hideTimer);
  slot.classList.add('leaving');
  _hideTimer = setTimeout(() => {
    slot.classList.remove('leaving');
    slot.classList.add('hidden');
    _hideTimer = null;
    _updateToggleVisibility();
  }, HIDE_TRANSITION_MS);
}

function _isVisible() {
  if (_currentMode() === 'docked') {
    const dock = _getDockSlot();
    return !!dock && !dock.classList.contains('hidden');
  }
  const slot = _getFloatSlot();
  return !!slot && !slot.classList.contains('hidden');
}

function _setGesturing(active) {
  document.body.classList.toggle('chat-gesturing', active);
}

function _wireInteract(slot) {
  if (typeof window.interact !== 'function') return;
  window
    .interact(slot)
    .draggable({
      allowFrom: '#chat-panel-head',
      inertia: false,
      modifiers: [
        window.interact.modifiers.restrictRect({
          restriction: 'parent',
          endOnly: false,
        }),
      ],
      listeners: {
        start() {
          _setGesturing(true);
        },
        move(event) {
          // Drag/resize only meaningful in floating mode; the docked panel
          // is statically positioned. interact.js still fires `move` on the
          // hidden float-slot element if something synthesises events, so
          // guard explicitly.
          if (_currentMode() === 'docked') return;
          const x = (parseFloat(slot.dataset.x) || 0) + event.dx;
          const y = (parseFloat(slot.dataset.y) || 0) + event.dy;
          _applyGeometry(slot, {
            x,
            y,
            width: slot.offsetWidth,
            height: slot.offsetHeight,
          });
        },
        end() {
          _setGesturing(false);
          if (_currentMode() === 'floating') _persistFromSlot(slot, true);
        },
      },
    })
    .resizable({
      edges: { left: true, right: true, top: true, bottom: true },
      margin: 10,
      modifiers: [
        window.interact.modifiers.restrictSize({
          min: { width: MIN_WIDTH, height: MIN_HEIGHT },
        }),
      ],
      listeners: {
        start() {
          _setGesturing(true);
        },
        move(event) {
          if (_currentMode() === 'docked') return;
          const x = (parseFloat(slot.dataset.x) || 0) + event.deltaRect.left;
          const y = (parseFloat(slot.dataset.y) || 0) + event.deltaRect.top;
          _applyGeometry(slot, {
            x,
            y,
            width: event.rect.width,
            height: event.rect.height,
          });
        },
        end() {
          _setGesturing(false);
          if (_currentMode() === 'floating') _persistFromSlot(slot, true);
        },
      },
    });
}

function _persistFromSlot(slot, visible) {
  // Only persist floating geometry; docked mode has no geometry to remember.
  _persist({
    visible,
    x: parseFloat(slot.dataset.x) || 0,
    y: parseFloat(slot.dataset.y) || 0,
    width: slot.offsetWidth,
    height: slot.offsetHeight,
  });
}

function _persistVisibility(visible) {
  // In docked mode we still want to remember whether the user hid the panel
  // so a reload keeps it closed. Geometry comes from the last floating session.
  const cur = _readState();
  _persist({ ...cur, visible });
}

function _toggle() {
  if (_isVisible()) {
    if (_currentMode() === 'floating') {
      const slot = _getFloatSlot();
      if (slot) _persistFromSlot(slot, false);
    } else {
      _persistVisibility(false);
    }
    _hide();
  } else {
    _show(_readState());
    if (_currentMode() === 'floating') {
      _persistFromSlot(_getFloatSlot(), true);
    } else {
      _persistVisibility(true);
    }
  }
}

function _toggleMode() {
  const prev = _currentMode();
  const next = prev === 'docked' ? 'floating' : 'docked';
  // Capture visibility BEFORE flipping mode — _isVisible() reads the slot
  // matching the current mode, so checking after the flip would query the
  // wrong slot (which is still hidden until reparent completes).
  const wasVisible = _isVisible();
  _userChoseModeSession = true;
  _setMode(next);
  if (wasVisible) {
    if (next === 'docked') _reparentToDock();
    else _reparentToFloat();
    _focusInput();
  }
  _updateToggleVisibility();
}

function _onResize() {
  // Auto-switch mode on viewport changes ONLY if the user hasn't made an
  // explicit choice this session. Same pattern as video-overlay.js.
  if (!_userChoseModeSession) {
    const wantDocked = _viewportAllowsDock();
    const isDocked = _currentMode() === 'docked';
    const wasVisible = _isVisible();
    if (wantDocked && !isDocked) {
      _setMode('docked');
      if (wasVisible) _reparentToDock();
    } else if (!wantDocked && isDocked) {
      _setMode('floating');
      if (wasVisible) _reparentToFloat();
    }
  }
  // Re-evaluate toggle visibility regardless (for the user-chose-floating
  // case where they shrunk the window — hide the dock button).
  _setMode(_currentMode());
  _updateToggleVisibility();

  // Reclamp floating geometry if visible.
  if (_currentMode() === 'floating' && _isVisible()) {
    const slot = _getFloatSlot();
    if (!slot) return;
    _applyGeometry(slot, {
      x: parseFloat(slot.dataset.x) || 0,
      y: parseFloat(slot.dataset.y) || 0,
      width: slot.offsetWidth,
      height: slot.offsetHeight,
    });
    _persistFromSlot(slot, true);
  }
}

/** Hide the bottom-right Quix toggle button when the panel is docked AND
 *  open — the dock sidebar IS the panel, so the floating button is just
 *  visual noise overlapping the sidebar (issue from screenshots). Show it
 *  in floating mode (always) and in docked mode when panel is closed (so
 *  user has a way to reopen). */
function _updateToggleVisibility() {
  const toggle = document.getElementById('chat-toggle');
  if (!toggle) return;
  const docked = _currentMode() === 'docked';
  const visible = _isVisible();
  toggle.style.display = docked && visible ? 'none' : '';
}

export function initChatOverlay() {
  const slot = _getFloatSlot();
  const toggle = document.getElementById('chat-toggle');
  if (!slot || !toggle) return;

  // Pick initial mode from viewport (not persisted).
  _setMode(_viewportAllowsDock() ? 'docked' : 'floating');

  _wireInteract(slot);

  const modeBtn = document.getElementById('chat-mode-toggle');
  if (modeBtn) modeBtn.addEventListener('click', _toggleMode);

  const state = _readState();
  if (state.visible) {
    _show(state);
  } else if (state._firstVisit) {
    _hide();
    setTimeout(() => {
      _show(_readState());
      if (_currentMode() === 'floating') {
        _persistFromSlot(_getFloatSlot(), true);
      } else {
        _persistVisibility(true);
      }
    }, 1500);
  } else {
    _hide();
  }

  toggle.addEventListener('click', _toggle);
  window.addEventListener('resize', _onResize);
  _updateToggleVisibility();

  // Click-to-focus inside panel (works in both modes since the listener is
  // on the panel itself, not the slot).
  const panel = _getPanel();
  if (panel) {
    panel.addEventListener('click', (ev) => {
      const target = ev.target;
      if (target.closest('button, textarea, input, a')) return;
      _focusInput();
    });
  }
}
