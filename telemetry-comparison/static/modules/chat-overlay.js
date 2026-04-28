/**
 * Floating chat panel — drag/resize via interact.js, show/hide via the
 * bottom-right toggle button, geometry persisted in localStorage.
 *
 * Slice 1: floating-only (no docked mode). Adds a `position:fixed` panel
 * wrapper at `#chat-float-slot`, a toggle button at `#chat-toggle`, and
 * remembers position+size across reloads under
 * `telemetryExplorer.chatPanel.v1`. The panel mirrors `video-overlay.js`
 * idioms (interact.js, clamp-to-viewport on resize, versioned key) but
 * skips the dock/float reparenting machinery — chat is always floating.
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

function _readState() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULTS };
    return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch (_) {
    return { ...DEFAULTS };
  }
}

function _persist(state) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch (_) {
    /* localStorage may be partitioned/disabled — non-fatal. */
  }
}

function _defaultGeometry() {
  const vw = window.innerWidth || document.documentElement.clientWidth || 1280;
  const vh = window.innerHeight || document.documentElement.clientHeight || 720;
  const width = Math.min(DEFAULTS.width, Math.max(MIN_WIDTH, vw - 2 * VIEWPORT_MARGIN));
  const height = Math.min(DEFAULTS.height, Math.max(MIN_HEIGHT, vh - 2 * VIEWPORT_MARGIN));
  return {
    x: Math.max(VIEWPORT_MARGIN, vw - width - VIEWPORT_MARGIN),
    y: Math.max(VIEWPORT_MARGIN, vh - height - VIEWPORT_MARGIN - 64),
    width,
    height,
  };
}

function _clamp(geom) {
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

function _show(state) {
  const slot = document.getElementById('chat-float-slot');
  const toggle = document.getElementById('chat-toggle');
  if (!slot) return;
  // Resolve geometry: stored if user has positioned before, else default.
  const hasStored = state.x >= 0 && state.y >= 0;
  const geom = hasStored
    ? { x: state.x, y: state.y, width: state.width, height: state.height }
    : _defaultGeometry();
  _applyGeometry(slot, geom);
  slot.classList.remove('hidden');
  if (toggle) toggle.setAttribute('aria-expanded', 'true');
  // Focus the input on every show so the user can type immediately. Defer
  // one frame so the panel is laid out before focus calls scrollIntoView.
  requestAnimationFrame(_focusInput);
}

function _hide() {
  const slot = document.getElementById('chat-float-slot');
  const toggle = document.getElementById('chat-toggle');
  if (slot) slot.classList.add('hidden');
  if (toggle) toggle.setAttribute('aria-expanded', 'false');
}

function _isVisible() {
  const slot = document.getElementById('chat-float-slot');
  return !!slot && !slot.classList.contains('hidden');
}

function _setGesturing(active) {
  // Toggling this class on body lets us disable text selection globally
  // during a drag/resize gesture so dragging over the message list text
  // doesn't accidentally highlight it. Released on gesture end.
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
          _persistFromSlot(slot, true);
        },
      },
    })
    .resizable({
      // All four edges are resizable. interact.js gives the resize hit zone
      // priority over draggable; `margin` controls how many pixels inward
      // from each edge count as the resize zone. 10 px is comfortable to
      // hit while leaving the bulk of the header free for drag.
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
          _persistFromSlot(slot, true);
        },
      },
    });
}

function _persistFromSlot(slot, visible) {
  _persist({
    visible,
    x: parseFloat(slot.dataset.x) || 0,
    y: parseFloat(slot.dataset.y) || 0,
    width: slot.offsetWidth,
    height: slot.offsetHeight,
  });
}

function _toggle() {
  if (_isVisible()) {
    const slot = document.getElementById('chat-float-slot');
    if (slot) _persistFromSlot(slot, false);
    _hide();
  } else {
    _show(_readState());
    _persistFromSlot(document.getElementById('chat-float-slot'), true);
  }
}

function _onResize() {
  if (!_isVisible()) return;
  const slot = document.getElementById('chat-float-slot');
  if (!slot) return;
  _applyGeometry(slot, {
    x: parseFloat(slot.dataset.x) || 0,
    y: parseFloat(slot.dataset.y) || 0,
    width: slot.offsetWidth,
    height: slot.offsetHeight,
  });
  _persistFromSlot(slot, true);
}

export function initChatOverlay() {
  const slot = document.getElementById('chat-float-slot');
  const toggle = document.getElementById('chat-toggle');
  if (!slot || !toggle) return;

  _wireInteract(slot);

  const state = _readState();
  if (state.visible) {
    _show(state);
  } else {
    _hide();
  }

  toggle.addEventListener('click', _toggle);
  window.addEventListener('resize', _onResize);

  // Click anywhere in the panel that isn't already an interactive element
  // → focus the input. Lets the user click the message list / header to
  // start typing without aiming at the textarea.
  slot.addEventListener('click', (ev) => {
    const target = ev.target;
    if (target.closest('button, textarea, input, a')) return;
    _focusInput();
  });
}
