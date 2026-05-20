/**
 * Marker-drag frame preview overlay.
 *
 * While the user drags the chart marker, this module shows a small thumbnail
 * of the closest video frame near the pointer. It does NOT touch the
 * <video> element — the existing Round 5 deferred-seek path in sync.js
 * still owns the single seek that fires on pointerup. This module is a
 * pure read-only consumer of marker time. Skill contract:
 * .claude/skills/video-seeking/SKILL.md (unchanged).
 *
 * Module shape:
 *   initThumbPreview(syncMeta) — call on every lap load. `syncMeta.thumbs` is
 *     the block from the sidecar JSON; absent → module is a no-op for that
 *     lap (drag works without preview, identical to pre-feature behaviour).
 *   showThumbPreviewAt(clientX, clientY, normPos) — call from chart drag
 *     handlers; recomputes tile, paints overlay, positions near pointer.
 *   hideThumbPreview() — call on pointerup/pointercancel.
 *
 * Tile-index math comes from the sidecar's `ms_per_tile`; we look up the
 * marker's video time via lookupTmsForNormPos so timestamp drift between
 * sync samples and sprite samples is bounded by sub-tile interpolation.
 */

import { lookupTmsForNormPos } from './sync.js';
import { videoState } from './state.js';
import { debugLog } from './debug-overlay.js';

const OVERLAY_ID = 'thumb-preview-overlay';
const POINTER_OFFSET_X = 12;
const POINTER_OFFSET_Y = 12;

// Per-lap state. Cleared on every initThumbPreview() so a lap without a
// `thumbs` block leaves the module dormant.
let _meta = null;          // sidecar `thumbs` block, frozen for the current lap
let _spriteUrl = null;     // /api/video/{sid}/{lap}/thumbs.jpg
let _spriteImg = null;     // Image() preload reference; kept alive so the
                           // browser doesn't evict the decoded bitmap

/**
 * Lazily build the overlay element. Idempotent — safe to call from any drag
 * handler. The element is fixed-position so it floats above any chart layout
 * without participating in chart layout.
 */
function _ensureOverlay() {
  let el = document.getElementById(OVERLAY_ID);
  if (el) return el;
  el = document.createElement('div');
  el.id = OVERLAY_ID;
  el.style.cssText = [
    'position: fixed',
    'top: 0',
    'left: 0',
    'display: none',
    'pointer-events: none',
    'z-index: 9000',
    'background-repeat: no-repeat',
    // Subtle border + shadow keeps the tile readable against light or dark
    // chart backgrounds without visually competing with the marker.
    'border: 1px solid rgba(255,255,255,0.6)',
    'box-shadow: 0 4px 12px rgba(0,0,0,0.5)',
    'border-radius: 2px',
  ].join(';');
  document.body.appendChild(el);
  return el;
}

/**
 * Build the sprite URL for the currently-loaded lap. Returns null if we
 * don't have enough state to construct it (no lap selected yet).
 */
function _spriteUrlForCurrentLap() {
  const idx = videoState.currentLapIdx;
  const sel = videoState.laps?.[idx];
  if (!sel) return null;
  const sid = sel.key?.session_id;
  const lap = sel.lap;
  if (sid == null || lap == null) return null;
  return `/api/video/${encodeURIComponent(sid)}/${lap}/thumbs.jpg`;
}

/**
 * Initialize per-lap state. Call from video.js after buildSyncLookups().
 * Pass the parsed sidecar `sync` object; we read `sync.thumbs` ourselves.
 */
export function initThumbPreview(syncMeta) {
  hideThumbPreview();
  _meta = null;
  _spriteImg = null;
  _spriteUrl = null;

  const thumbs = syncMeta && syncMeta.thumbs;
  if (!thumbs || !thumbs.tile_w || !thumbs.tile_h || !thumbs.ms_per_tile) {
    debugLog('[thumbs] not available for this lap — drag preview disabled');
    return;
  }

  _spriteUrl = _spriteUrlForCurrentLap();
  if (!_spriteUrl) {
    debugLog('[thumbs] no current lap selection — preview disabled');
    return;
  }

  _meta = thumbs;
  // Preload the sprite. Held alive on a module-level reference so the
  // browser keeps the decoded bitmap warm. First-drag flash is acceptable
  // per spec §10 risk row "First-drag flash"; we don't gate on .decode().
  const img = new Image();
  img.src = _spriteUrl;
  _spriteImg = img;
  debugLog(
    `[thumbs] loaded tiles=${thumbs.tiles} tile=${thumbs.tile_w}x${thumbs.tile_h} ms_per_tile=${thumbs.ms_per_tile}`,
  );
  if (thumbs._speculative) {
    debugLog('[thumbs] speculative metadata; first request will trigger lazy generation');
  }
}

/**
 * Compute the (col, row) tile index for the given normalised marker position.
 * Returns null when we don't have enough info to render.
 */
function _tileFor(normPos) {
  if (!_meta) return null;
  const t_ms = lookupTmsForNormPos(normPos);
  if (t_ms == null || !Number.isFinite(t_ms)) return null;
  const total = _meta.tiles || 100;
  const cols = _meta.cols || 10;
  let idx = Math.floor(t_ms / Math.max(1, _meta.ms_per_tile));
  if (idx < 0) idx = 0;
  if (idx > total - 1) idx = total - 1;
  return { col: idx % cols, row: Math.floor(idx / cols), idx };
}

/**
 * Position the overlay near the pointer, clamped to the viewport. Default
 * placement is below+right of the cursor; flips to the opposite side when
 * the preview would otherwise clip the right or bottom edge.
 */
function _positionOverlay(el, clientX, clientY) {
  const w = _meta.tile_w;
  const h = _meta.tile_h;
  const vpW = window.innerWidth || document.documentElement.clientWidth;
  const vpH = window.innerHeight || document.documentElement.clientHeight;

  let left = clientX + POINTER_OFFSET_X;
  let top = clientY + POINTER_OFFSET_Y;
  if (left + w > vpW - 4) {
    left = clientX - w - POINTER_OFFSET_X;
  }
  if (top + h > vpH - 4) {
    top = clientY - h - POINTER_OFFSET_Y;
  }
  if (left < 4) left = 4;
  if (top < 4) top = 4;
  el.style.left = `${Math.round(left)}px`;
  el.style.top = `${Math.round(top)}px`;
}

/**
 * Show or update the preview tile for the current marker position.
 * `normPos` is the value just passed to updateMarker (clamped 0..1).
 */
export function showThumbPreviewAt(clientX, clientY, normPos) {
  if (!_meta || !_spriteUrl) return;
  const tile = _tileFor(normPos);
  if (!tile) return;

  const el = _ensureOverlay();
  el.style.width = `${_meta.tile_w}px`;
  el.style.height = `${_meta.tile_h}px`;
  el.style.backgroundImage = `url("${_spriteUrl}")`;
  el.style.backgroundSize = `${_meta.tile_w * _meta.cols}px ${_meta.tile_h * _meta.rows}px`;
  el.style.backgroundPosition = `-${tile.col * _meta.tile_w}px -${tile.row * _meta.tile_h}px`;
  _positionOverlay(el, clientX, clientY);
  el.style.display = 'block';
}

/**
 * Hide the overlay. Safe to call repeatedly. Idempotent.
 */
export function hideThumbPreview() {
  const el = document.getElementById(OVERLAY_ID);
  if (el) el.style.display = 'none';
}
