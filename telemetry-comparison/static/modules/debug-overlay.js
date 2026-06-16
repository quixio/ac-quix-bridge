/**
 * On-page debug overlay — top-right log panel for tablet diagnostics.
 *
 * Why this exists: Quix Cloud-deployed Telemetry Explorer runs on Ludvík's
 * tablet where remote DevTools is not available. We need a way to read
 * structured `<video>` events and rAF-pause hooks while reproducing tablet-
 * only bugs. Routing the same lines to a fixed-position div solves it.
 *
 * Activation (no-op unless one of these is true at module-import time):
 *   - URL has `?debug=1` (or just `?debug`)
 *   - localStorage.getItem('te-debug') === '1'
 *
 * Public surface:
 *   debugLog(msg) — append a line to the buffer + DOM. No-op when inactive.
 *
 * The overlay holds the last DEBUG_OVERLAY_MAX_LINES log entries (older lines
 * scroll off the top). A small ✕ button hides it for the current session
 * (without clearing the toggle); a Copy button copies the whole buffer to
 * the clipboard so Ludvík can paste back instead of screenshotting.
 *
 * Safe to import always: if neither toggle is set the module short-circuits
 * to no-op and never appends DOM. No CSS import side-effects either.
 */

const DEBUG_OVERLAY_MAX_LINES = 30;
const DEBUG_OVERLAY_STYLE = `
position: fixed;
top: 8px;
right: 8px;
width: 340px;
max-height: 50vh;
z-index: 9999;
background: rgba(15, 17, 23, 0.85);
color: #c9d1d9;
border: 1px solid rgba(255, 255, 255, 0.15);
border-radius: 6px;
font-family: ui-monospace, Menlo, Consolas, monospace;
font-size: 11px;
line-height: 1.35;
padding: 6px 8px;
display: flex;
flex-direction: column;
gap: 4px;
pointer-events: auto;
box-shadow: 0 4px 14px rgba(0, 0, 0, 0.45);
`;

function _isActive() {
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.has('debug')) return true;
  } catch (_) {
    /* noop */
  }
  try {
    if (localStorage.getItem('te-debug') === '1') return true;
  } catch (_) {
    /* noop */
  }
  return false;
}

const _active = _isActive();
const _buffer = []; // ring of last N lines
let _preEl = null;
let _rootEl = null;

function _ensureDom() {
  if (_rootEl || !_active) return;
  if (typeof document === 'undefined') return;
  // Defer DOM append until body exists — module may import before DOM ready.
  if (!document.body) {
    document.addEventListener('DOMContentLoaded', _ensureDom, { once: true });
    return;
  }
  _rootEl = document.createElement('div');
  _rootEl.id = 'te-debug-overlay';
  _rootEl.style.cssText = DEBUG_OVERLAY_STYLE;

  const head = document.createElement('div');
  head.style.cssText =
    'display:flex; gap:6px; align-items:center; justify-content:space-between;';
  const title = document.createElement('span');
  title.textContent = 'TE debug';
  title.style.cssText = 'opacity:0.7; font-weight:600;';
  const btnRow = document.createElement('div');
  btnRow.style.cssText = 'display:flex; gap:4px;';

  const copyBtn = document.createElement('button');
  copyBtn.textContent = 'Copy';
  copyBtn.style.cssText =
    'background:transparent; border:1px solid rgba(255,255,255,0.25); color:#c9d1d9; cursor:pointer; padding:1px 6px; font:inherit; border-radius:3px;';
  copyBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    const text = _buffer.join('\n');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(() => {});
    }
  });

  const closeBtn = document.createElement('button');
  closeBtn.textContent = '✕';
  closeBtn.title = 'Hide for this session';
  closeBtn.style.cssText =
    'background:transparent; border:1px solid rgba(255,255,255,0.25); color:#c9d1d9; cursor:pointer; padding:1px 6px; font:inherit; border-radius:3px;';
  closeBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (_rootEl) _rootEl.style.display = 'none';
  });

  btnRow.appendChild(copyBtn);
  btnRow.appendChild(closeBtn);
  head.appendChild(title);
  head.appendChild(btnRow);

  _preEl = document.createElement('pre');
  _preEl.style.cssText =
    'margin:0; padding:0; overflow-y:auto; max-height:calc(50vh - 32px); white-space:pre-wrap; word-break:break-word;';

  _rootEl.appendChild(head);
  _rootEl.appendChild(_preEl);
  document.body.appendChild(_rootEl);

  // Render anything that was logged before the DOM was ready.
  _renderBuffer();
}

function _renderBuffer() {
  if (!_preEl) return;
  _preEl.textContent = _buffer.join('\n');
  // Auto-scroll to bottom so newest entries are always visible.
  _preEl.scrollTop = _preEl.scrollHeight;
}

function _appendLine(msg) {
  // Lightweight timestamp (mm:ss.SSS) so events can be correlated by hand.
  const d = new Date();
  const ts =
    String(d.getMinutes()).padStart(2, '0') +
    ':' +
    String(d.getSeconds()).padStart(2, '0') +
    '.' +
    String(d.getMilliseconds()).padStart(3, '0');
  _buffer.push(`${ts} ${msg}`);
  if (_buffer.length > DEBUG_OVERLAY_MAX_LINES) {
    _buffer.splice(0, _buffer.length - DEBUG_OVERLAY_MAX_LINES);
  }
  _renderBuffer();
}

// Public API — no-op when inactive so callers don't need to gate their calls.
export const debugLog = _active
  ? (msg) => {
      try {
        _appendLine(String(msg));
      } catch (_) {
        /* never throw from a logger */
      }
    }
  : () => {};

export const isDebugOverlayActive = () => _active;

if (_active) _ensureDom();
