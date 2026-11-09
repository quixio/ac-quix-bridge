/**
 * Theme core — classic script loaded synchronously from <head> so the theme
 * is resolved and applied to <html data-theme> BEFORE first paint.
 *
 * Resolution order (see resolveTheme): ?theme= query param > saved choice
 * (localStorage) > prefers-color-scheme > 'dark'. The query param wins so the
 * Quix portal embedding us in an iframe can force a theme; it is never
 * persisted — only an explicit toggle click writes localStorage.
 *
 * Also owns the chart-side half of the theme (Plotly colours + trace palette),
 * since Plotly colours cannot come from CSS variables. CSS tokens live in
 * styles.css; keep the two in sync.
 *
 * Exposes `window.TETheme`. No imports/exports so it works as a classic script.
 */
(function (root) {
  'use strict';

  var STORAGE_KEY = 'te-theme';
  var DEFAULT_THEME = 'dark';

  function normalize(value) {
    return value === 'light' || value === 'dark' ? value : null;
  }

  /**
   * Pure resolver. `system` is 'light' | 'dark' | undefined (no signal).
   * Invalid values at any level are ignored and fall through to the next.
   */
  function resolveTheme(opts) {
    var o = opts || {};
    return normalize(o.query) || normalize(o.saved) || normalize(o.system) || DEFAULT_THEME;
  }

  function readQuery() {
    try {
      return new URLSearchParams(root.location.search).get('theme');
    } catch {
      return null;
    }
  }

  function readSaved() {
    try {
      return root.localStorage.getItem(STORAGE_KEY);
    } catch {
      return null; // storage blocked (e.g. third-party iframe)
    }
  }

  function readSystem() {
    try {
      if (root.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
      if (root.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
    } catch {
      /* no matchMedia */
    }
    return undefined;
  }

  function detectTheme() {
    return resolveTheme({ query: readQuery(), saved: readSaved(), system: readSystem() });
  }

  function currentTheme() {
    var attr = root.document && root.document.documentElement.getAttribute('data-theme');
    return normalize(attr) || DEFAULT_THEME;
  }

  function applyTheme(theme) {
    var next = normalize(theme) || DEFAULT_THEME;
    var prev = currentTheme();
    root.document.documentElement.setAttribute('data-theme', next);
    if (next !== prev) {
      root.document.dispatchEvent(
        new root.CustomEvent('themechange', { detail: { theme: next, previous: prev } }),
      );
    }
    return next;
  }

  /** Apply + persist an explicit user choice. */
  function setTheme(theme) {
    var next = normalize(theme);
    if (!next) return currentTheme();
    try {
      root.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* non-fatal: choice just won't survive a reload */
    }
    return applyTheme(next);
  }

  function toggleTheme() {
    return setTheme(currentTheme() === 'dark' ? 'light' : 'dark');
  }

  // -------------------------------------------------------------------------
  // Chart theming (Plotly takes literal colours, not CSS variables).
  // -------------------------------------------------------------------------

  // Index-aligned: TRACE_PALETTES.light[i] is the white-background-safe
  // counterpart of TRACE_PALETTES.dark[i], so a trace keeps its identity
  // across a theme switch (see mapTraceColor).
  var TRACE_PALETTES = {
    dark: [
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
    ],
    light: [
      '#2563eb',
      '#d97706',
      '#047857',
      '#dc2626',
      '#7c3aed',
      '#db2777',
      '#0e7490',
      '#4d7c0f',
      '#c2410c',
      '#6d28d9',
      '#0f766e',
      '#a21caf',
      '#92400e',
      '#0369a1',
      '#3f6212',
      '#a16207',
    ],
  };

  var CHART_THEMES = {
    dark: {
      paper: '#222229',
      plot: '#222229',
      text: '#eaedf0',
      muted: '#a8b1c0',
      grid: '#3e3e46',
      markerLine: '#ffffff',
      annotationBg: 'rgba(15,17,23,0.9)',
      annotationMutedBg: 'rgba(15,17,23,0.7)',
      cornerLabelBg: 'rgba(26,26,26,0.6)',
      trackBase: '#3e3e46',
      badgeOutline: '#1a1a1a',
      dotOutline: '#ffffff',
      hoverlabel: null, // Plotly default (trace-coloured) — unchanged
      trackColors: null, // server-provided severity colours
    },
    light: {
      paper: '#ffffff',
      plot: '#ffffff',
      text: '#1a1d23',
      muted: '#4b5565',
      grid: '#e3e7ee',
      markerLine: '#111827',
      annotationBg: 'rgba(255,255,255,0.92)',
      annotationMutedBg: 'rgba(255,255,255,0.85)',
      cornerLabelBg: 'rgba(255,255,255,0.75)',
      trackBase: '#d5dbe4',
      badgeOutline: '#ffffff',
      dotOutline: '#111827',
      hoverlabel: { bgcolor: '#ffffff', bordercolor: '#c5ccd6', font: { color: '#1a1d23' } },
      // Server colours are pastel (tuned for dark); darker equivalents for white.
      trackColors: {
        hairpin: '#dc2626',
        tight: '#c2410c',
        sweeper: '#a16207',
        straight: '#047857',
        start_finish: '#111827',
        marker: '#92400e',
        track_dot: '#dc2626',
      },
    },
  };

  function chartTheme(theme) {
    return CHART_THEMES[normalize(theme) || currentTheme()];
  }

  function traceColors(theme) {
    return TRACE_PALETTES[normalize(theme) || currentTheme()];
  }

  /** Base Plotly layout fragment (backgrounds, fonts, axes, hover labels). */
  function plotlyLayout(theme) {
    var t = chartTheme(theme);
    var layout = {
      paper_bgcolor: t.paper,
      plot_bgcolor: t.plot,
      font: { color: t.text, size: 11 },
      xaxis: { color: t.muted, gridcolor: t.grid, zerolinecolor: t.grid },
      yaxis: { color: t.muted, gridcolor: t.grid, zerolinecolor: t.grid, autorange: true },
    };
    if (t.hoverlabel) layout.hoverlabel = t.hoverlabel;
    return layout;
  }

  /** Overlay the theme's severity colours on the server-provided ones. */
  function trackColors(serverColors, theme) {
    var override = chartTheme(theme).trackColors;
    return override ? Object.assign({}, serverColors, override) : serverColors;
  }

  /** Translate a palette colour from one theme to the other (identity if unknown). */
  function mapTraceColor(color, fromTheme, toTheme) {
    var from = TRACE_PALETTES[normalize(fromTheme)];
    var to = TRACE_PALETTES[normalize(toTheme)];
    if (!from || !to) return color;
    var idx = from.indexOf(String(color).toLowerCase());
    return idx === -1 ? color : to[idx];
  }

  root.TETheme = {
    STORAGE_KEY: STORAGE_KEY,
    DEFAULT_THEME: DEFAULT_THEME,
    resolveTheme: resolveTheme,
    detectTheme: detectTheme,
    currentTheme: currentTheme,
    applyTheme: applyTheme,
    setTheme: setTheme,
    toggleTheme: toggleTheme,
    chartTheme: chartTheme,
    traceColors: traceColors,
    plotlyLayout: plotlyLayout,
    trackColors: trackColors,
    mapTraceColor: mapTraceColor,
  };

  // Apply immediately (before first paint when loaded from <head>).
  if (root.document && root.document.documentElement) {
    applyTheme(detectTheme());

    // Follow OS changes live, but only while the user hasn't made a choice
    // and no ?theme= override is pinning the theme.
    try {
      var mq = root.matchMedia('(prefers-color-scheme: light)');
      var onSystemChange = function () {
        if (!normalize(readQuery()) && !normalize(readSaved())) applyTheme(detectTheme());
      };
      if (mq.addEventListener) mq.addEventListener('change', onSystemChange);
      else if (mq.addListener) mq.addListener(onSystemChange);
    } catch {
      /* no matchMedia */
    }
  }
})(typeof window !== 'undefined' ? window : globalThis);
