/**
 * Theme toggle wiring + live re-theming of already-rendered charts.
 *
 * theme-core.js (classic script in <head>) owns resolution/persistence and
 * fires `themechange` on document; this module reacts to it. Plotly colours
 * are literal, so existing charts are relayouted/restyled in place rather than
 * re-fetched.
 */

import { appState } from './state.js';
import { updateMarker, highlightVideoLapTrace } from './sync.js';
import { buildCornerShapes, buildCornerAnnotations } from './charts.js';

const TOGGLE_ID = 'theme-toggle';

export function updateToggleButton(btn, theme) {
  if (!btn) return;
  const toLight = theme === 'dark';
  const label = toLight ? 'Switch to light theme' : 'Switch to dark theme';
  btn.setAttribute('aria-label', label);
  btn.setAttribute('title', label);
  btn.setAttribute('aria-pressed', String(theme === 'light'));
}

export function rethemeCharts(prev, next) {
  const T = window.TETheme;
  const ct = T.chartTheme(next);
  const layout = T.plotlyLayout(next);

  appState.plotTraces.forEach((traces, pi) => {
    const div = appState.plotDivs[pi];
    traces.forEach((t) => {
      t.color = T.mapTraceColor(t.color, prev, next);
    });
    if (!div || !div.data) return;

    traces.forEach((t, i) => {
      Plotly.restyle(div, { 'line.color': t.color }, [i]);
    });

    const update = {
      paper_bgcolor: layout.paper_bgcolor,
      plot_bgcolor: layout.plot_bgcolor,
      'font.color': layout.font.color,
      'xaxis.color': layout.xaxis.color,
      'xaxis.gridcolor': layout.xaxis.gridcolor,
      'xaxis.zerolinecolor': layout.xaxis.zerolinecolor,
      'yaxis.color': layout.yaxis.color,
      'yaxis.gridcolor': layout.yaxis.gridcolor,
      'yaxis.zerolinecolor': layout.yaxis.zerolinecolor,
      hoverlabel: layout.hoverlabel || { bgcolor: null, bordercolor: null, font: { color: null } },
    };

    const marker = div.layout?.shapes?.[0];
    if (marker) {
      const cornersOn = (div._cornerAnnotations || []).length > 0;
      update.shapes = [
        { ...marker, line: { ...marker.line, color: ct.markerLine } },
        ...(cornersOn ? buildCornerShapes() : []),
      ];
      if (cornersOn) div._cornerAnnotations = buildCornerAnnotations();
    }
    Plotly.relayout(div, update);
  });

  // Rewrites value annotations (themed in sync.js) and moves the track dot.
  updateMarker(appState.markerPosition, false);

  if (window.trackData && window.trackConfig && typeof window.renderTrackMap === 'function') {
    window.renderTrackMap();
  }

  // Legend glow uses the (now remapped) trace colour.
  if (appState.highlightedLabel && appState.plotDivs.length) {
    highlightVideoLapTrace(appState.highlightedLabel);
  }
}

export function initThemeToggle() {
  const T = window.TETheme;
  const btn = document.getElementById(TOGGLE_ID);
  updateToggleButton(btn, T.currentTheme());
  if (btn) btn.addEventListener('click', () => T.toggleTheme());

  document.addEventListener('themechange', (ev) => {
    const { theme, previous } = ev.detail;
    updateToggleButton(document.getElementById(TOGGLE_ID), theme);
    rethemeCharts(previous, theme);
  });
}
