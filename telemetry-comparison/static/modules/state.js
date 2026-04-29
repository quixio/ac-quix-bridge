/**
 * Shared cross-module state + constants for the Telemetry Explorer frontend.
 *
 * Every module that touches `appState` or `videoState` MUST import them from
 * here — NEVER re-declare them locally. All modules must hold the same object
 * reference so mutations propagate.
 *
 * Non-module interop: `modules/track-map.js` (classic script) reads the
 * implicit globals `trackData`, `trackConfig`, `markerPosition`, `trackZoom`
 * and writes `trackBaseRange`, `trackZoom`, `window._markerTraceIdx`. Because
 * modules run in their own scope, these values are published onto `window.*`
 * here so both sides see the same values. Helper getters/setters below are
 * the recommended access path from module code; reading `window.trackData`
 * directly is equivalent.
 */

export const TRACE_COLORS = [
  '#4f8ef7',
  '#f59e0b',
  '#059669',
  '#ff3f41',
  '#a78bfa',
  '#ec4899',
  '#06b6d4',
  '#84cc16',
  '#f97316',
  '#8b5cf6',
  '#14b8a6',
  '#e879f9',
  '#fb923c',
  '#38bdf8',
  '#a3e635',
  '#fbbf24',
];

export const ROW_COLORS = TRACE_COLORS;

export const PART_COLS = [
  'environment',
  'test_rig',
  'experiment',
  'driver',
  'track',
  'carModel',
  'session_id',
];

export const PART_LABELS = {
  environment: 'Env',
  test_rig: 'Rig',
  experiment: 'Experiment',
  driver: 'Driver',
  track: 'Track',
  carModel: 'Car',
  session_id: 'Session',
};

// Mirrors the dark palette in styles.css :root + tailwind.config.
export const PLOTLY_LAYOUT = {
  paper_bgcolor: '#222229',
  plot_bgcolor: '#222229',
  font: { color: '#eaedf0', size: 11 },
  legend: { orientation: 'v', x: 1.02, y: 1, font: { size: 8 } },
  margin: { t: 10, r: 60, b: 40, l: 55 },
  height: 240,
  xaxis: { color: '#a8b1c0', gridcolor: '#3e3e46', zerolinecolor: '#3e3e46' },
  yaxis: { color: '#a8b1c0', gridcolor: '#3e3e46', zerolinecolor: '#3e3e46', autorange: true },
};

export const DEFAULT_ACTIVE = new Set(['speedKmh', 'gas', 'brake', 'rpms']);

export const CAT_ORDER = [
  'Inputs',
  'Motion',
  'Engine',
  'Tyres',
  'Suspension & Brakes',
  'Environment',
  'Car State',
  'Session',
];

export const MAX_VISIBLE = 8;
export const MAX_READOUTS = 6;
export const MAX_TRACE_ANNOTATIONS = 6;

// Video-frame-accurate sync: use requestVideoFrameCallback when available.
// It fires once per displayed frame with the exact mediaTime of that frame,
// eliminating drift from rAF polling + browser decode lag.
export const HAS_RVFC =
  typeof HTMLVideoElement !== 'undefined' &&
  'requestVideoFrameCallback' in HTMLVideoElement.prototype;

// Fallback: if requestVideoFrameCallback is absent, poll via rAF at ~30Hz.
export const VIDEO_RAF_INTERVAL_MS = 1000 / 30;

// ---------------------------------------------------------------------------
// Mutable app-wide state. Exported as one object literal so every importer
// holds the same reference and mutations from any module are visible to all.
// ---------------------------------------------------------------------------

export const appState = {
  sessions: [], // loaded once on tab open; filtered client-side for dropdowns
  channels: {},
  rowCount: 0,
  plotDivs: [],
  plotSignals: [],
  plotTraces: [],
  markerPosition: 0, // duplicated to window.markerPosition (see below) for track-map.js
  highlightedLabel: null,
  globalTraceIdx: 0,
};

// ---------------------------------------------------------------------------
// Video sync state — shape is frozen by .claude/skills/video-seeking/SKILL.md.
// Do NOT rename fields without updating the SKILL doc and every consumer.
// ---------------------------------------------------------------------------

export const videoState = {
  element: null, // <video> reference (set once at init)
  laps: [], // currently selectable laps (subset of plot selections)
  currentLapIdx: -1, // index in videoState.laps, -1 = none loaded
  currentLoadToken: 0, // monotonic, used to ignore stale async loads
  frames: null, // sorted-by-t_ms array of {t_ms, normPos} for the loaded lap
  framesByNd: null,
  isPlaying: false, // true between 'play' and 'pause'/'ended' events
  loadingShownAt: 0, // Date.now() when overlay was last shown; used for min-display guard
  _pendingSeekTime: null, // seek-coalesce: stash of newest target while v.seeking===true; drained on 'seeked'
  _prefetchAbort: null, // Round 6: AbortController for the per-lap full-file background prefetch; aborted on lap switch
  _prefetchDone: false, // Round 7: flips true once the prefetch fetch resolves with response.ok; gates live-preview branch in syncVideoFromMarker; reset to false on every new lap load
  _dragActive: false, // Round 7.3: chart-marker drag in flight — gates the seeked-listener drain so it doesn't re-issue stashed targets mid-scrub. flushPendingSeek owns the drain on pointerup.
  _lastLiveSeekAt: 0, // Round 8: Date.now() ms of last paused-mode live scrub seek; used to throttle to ~10 Hz so the decoder pipeline keeps up.
};

// ---------------------------------------------------------------------------
// Globals shared with the non-module track-map.js. track-map.js reads these
// as implicit globals and writes trackBaseRange + trackZoom the same way.
// We seed them here so code paths that read the values before track-map
// populates them see `undefined`-friendly defaults (null / 0 / 1).
// ---------------------------------------------------------------------------

if (typeof window !== 'undefined') {
  if (window.trackData === undefined) window.trackData = null;
  if (window.trackConfig === undefined) window.trackConfig = null;
  if (window.markerPosition === undefined) window.markerPosition = 0;
  if (window.trackBaseRange === undefined) window.trackBaseRange = null;
  if (window.trackZoom === undefined) window.trackZoom = 1;
}

/**
 * Track-state getters — thin wrappers over window.* so module code has a
 * self-documenting read path. Callers outside this file should prefer these
 * over reading `window.trackData` directly.
 */
export function getTrackData() {
  return typeof window !== 'undefined' ? window.trackData : null;
}

export function getTrackConfig() {
  return typeof window !== 'undefined' ? window.trackConfig : null;
}

export function getTrackZoom() {
  return typeof window !== 'undefined' ? window.trackZoom || 1 : 1;
}

/**
 * trackData / trackConfig are populated by fetchTrack() (see data.js) which
 * writes the values back via these setters. We set BOTH the module-visible
 * window global (for track-map.js's implicit reads) and nothing else — the
 * setters exist only to keep the assignment site discoverable via grep.
 */
export function setTrackData(v) {
  if (typeof window !== 'undefined') window.trackData = v;
}

export function setTrackConfig(v) {
  if (typeof window !== 'undefined') window.trackConfig = v;
}

/**
 * markerPosition is duplicated: appState.markerPosition is the canonical
 * module-side value; window.markerPosition mirrors it so track-map.js's
 * `updateMarker(markerPosition, true)` call at the end of renderTrackMap()
 * reads the right value. updateMarker() in sync.js writes both.
 */
export function setMarkerPosition(v) {
  appState.markerPosition = v;
  if (typeof window !== 'undefined') window.markerPosition = v;
}
