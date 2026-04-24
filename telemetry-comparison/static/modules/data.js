/**
 * Data fetching + pure numerical utilities.
 *
 * Network endpoints are unchanged:
 *   GET /api/sessions[?<partition filters>]
 *   GET /api/track
 *   GET /api/track/config
 *   GET /api/channels
 *   GET /api/telemetry?<partition filters>&lap=N&signals=a,b,c
 */

import { appState, setTrackData, setTrackConfig } from './state.js';

export async function fetchSessions(filters) {
  const p = new URLSearchParams();
  if (filters) {
    for (const [k, v] of Object.entries(filters)) {
      if (v) p.set(k, v);
    }
  }
  const qs = p.toString();
  const res = await fetch('/api/sessions' + (qs ? '?' + qs : ''));
  if (!res.ok) {
    // Include the server's `detail` (often carries the real upstream status,
    // e.g. "Data lake returned 403 Forbidden") so the toast is actionable.
    let detail = '';
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* non-JSON response */
    }
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  const json = await res.json();
  return json.sessions || [];
}

export async function fetchTrack() {
  try {
    const [tRes, cRes] = await Promise.all([fetch('/api/track'), fetch('/api/track/config')]);
    setTrackData(await tRes.json());
    setTrackConfig(await cRes.json());
    // renderTrackMap() is defined in the non-module track-map.js.
    if (typeof window.renderTrackMap === 'function') window.renderTrackMap();
  } catch (e) {
    console.warn('Track data unavailable:', e);
  }
}

/**
 * Load the channels map and stash it on appState. Returns the map so callers
 * that also need to render chips (selections.js) don't have to re-read state.
 */
export async function fetchChannels() {
  const res = await fetch('/api/channels');
  appState.channels = await res.json();
  return appState.channels;
}

/**
 * Fetch telemetry for one selection (session partition keys + lap + signals).
 * Returns the raw response JSON (unchanged contract): { data: {...}, count }.
 */
export async function fetchTelemetry(sel, signals) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(sel.key)) {
    if (v) p.set(k, v);
  }
  p.set('lap', sel.lap);
  p.set('signals', signals.join(','));
  const r = await fetch('/api/telemetry?' + p);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

// ---------------------------------------------------------------------------
// Client-side partition filtering over the `sessions` array loaded once at
// tab open. Instant (pure array work, no network).
// ---------------------------------------------------------------------------

export function getDistinctValues(column, upstreamFilters) {
  let filtered = appState.sessions;
  for (const [col, val] of Object.entries(upstreamFilters)) {
    if (val) filtered = filtered.filter((s) => String(s[col]) === String(val));
  }
  const vals = [...new Set(filtered.map((s) => s[column]))].filter((v) => v !== undefined);
  vals.sort();
  return vals;
}

// ---------------------------------------------------------------------------
// Pure numerical helpers
// ---------------------------------------------------------------------------

export function downsample(x, y, maxPoints = 1500) {
  if (!x || x.length <= maxPoints) return { x, y };
  const step = x.length / maxPoints;
  const nx = [],
    ny = [];
  for (let i = 0; i < maxPoints; i++) {
    const idx = Math.round(i * step);
    nx.push(x[idx]);
    ny.push(y[idx]);
  }
  return { x: nx, y: ny };
}

/**
 * Linear-interpolate a y value at xTarget over (xArr, yArr). Binary search +
 * straddling-pair interpolation. Returns null if the arrays are empty.
 */
export function interpolateAt(xArr, yArr, xTarget) {
  if (!xArr || !xArr.length) return null;
  if (xTarget <= xArr[0]) return yArr[0];
  if (xTarget >= xArr[xArr.length - 1]) return yArr[yArr.length - 1];
  // Binary search
  let lo = 0,
    hi = xArr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xArr[mid] <= xTarget) lo = mid;
    else hi = mid;
  }
  const x0 = xArr[lo],
    x1 = xArr[hi];
  const y0 = yArr[lo],
    y1 = yArr[hi];
  if (x1 === x0) return y0;
  return y0 + ((y1 - y0) * (xTarget - x0)) / (x1 - x0);
}

/**
 * Generic keyed binary-search + linear interpolation over an array of objects.
 * Used by sync.js for (t_ms -> normPos) and (normPos -> t_ms) lookups.
 */
export function _interp(arr, keyFn, valFn, target) {
  if (!arr || !arr.length) return null;
  if (target <= keyFn(arr[0])) return valFn(arr[0]);
  if (target >= keyFn(arr[arr.length - 1])) return valFn(arr[arr.length - 1]);
  let lo = 0,
    hi = arr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (keyFn(arr[mid]) <= target) lo = mid;
    else hi = mid;
  }
  const k0 = keyFn(arr[lo]),
    k1 = keyFn(arr[hi]);
  if (k1 === k0) return valFn(arr[lo]);
  const frac = (target - k0) / (k1 - k0);
  return valFn(arr[lo]) + frac * (valFn(arr[hi]) - valFn(arr[lo]));
}
