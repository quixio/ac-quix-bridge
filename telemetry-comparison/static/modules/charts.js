/**
 * Plotly chart lifecycle: plot(), marker drag wiring, linked x-axes, corner
 * overlay, status bar.
 *
 * Imports updateMarker from sync.js; does NOT import anything from video.js.
 * Writes plotDivs / plotSignals / plotTraces onto appState so sync.js and
 * video.js can read them. plot() dispatches 'plot-complete' on document with
 * the selections array so video.js can populate its lap picker without
 * creating a charts→video import cycle.
 */

import { appState, PLOTLY_LAYOUT, videoState } from './state.js';
import { downsample, fetchTelemetry } from './data.js';
import { updateMarker, flushPendingSeek } from './sync.js';
import { getSelections, getActiveSignals, chartTitle } from './selections.js';
// Round 8.1: sprite-sheet preview overlay near the cursor disabled. The
// main <video> element is the one that should show the scrubbed frame —
// see syncVideoFromMarker's paused-mode throttled live scrub. Module is
// still present in the repo but no longer wired.
// import { showThumbPreviewAt, hideThumbPreview } from './thumb-preview.js';

export function setStatus(msg, isError = false) {
  const el = document.getElementById('status');
  el.innerHTML = msg;
  el.className = 'status-bar' + (isError ? ' error' : '');
}

// ---------------------------------------------------------------------------
// Corner overlay — drawn as shapes appended after the marker line (shapes[0]).
// Annotations live on div._cornerAnnotations so updateMarker can preserve
// them when it rewrites the annotations array on each marker move.
// ---------------------------------------------------------------------------

function buildCornerShapes() {
  const trackData = window.trackData;
  const trackConfig = window.trackConfig;
  if (!trackData?.corners || !trackConfig) return [];
  const colors = trackConfig.colors;
  return trackData.corners.map((c) => ({
    type: 'rect',
    xref: 'x',
    yref: 'paper',
    x0: c.start_norm,
    x1: c.end_norm,
    y0: 0,
    y1: 1,
    fillcolor: colors[c.severity],
    opacity: 0.18,
    line: { width: 0 },
    layer: 'below',
  }));
}

function buildCornerAnnotations() {
  const trackData = window.trackData;
  if (!trackData?.corners) return [];
  return trackData.corners.map((c) => ({
    xref: 'x',
    yref: 'paper',
    x: (c.start_norm + c.end_norm) / 2,
    y: 1,
    yanchor: 'top',
    text: c.label,
    showarrow: false,
    font: { size: 10, color: '#e2e8f0' },
    bgcolor: 'rgba(15,17,23,0.6)',
    borderpad: 2,
  }));
}

export function toggleCornerOverlay(plotIdx, enabled) {
  const div = appState.plotDivs[plotIdx];
  if (!div) return;
  // Preserve the marker shape (always shapes[0])
  const base = [div.layout.shapes[0]];
  const shapes = enabled ? base.concat(buildCornerShapes()) : base;
  // Store corner annotations separately so updateMarker can preserve them
  div._cornerAnnotations = enabled ? buildCornerAnnotations() : [];
  Plotly.relayout(div, { shapes });
  // Refresh so value annotations + corner labels coexist
  updateMarker(appState.markerPosition, false);
}

// ---------------------------------------------------------------------------
// Marker drag — pointerdown/pointermove/pointerup covers mouse, touch, and
// stylus in one API. setPointerCapture keeps the drag live even when the
// finger or cursor leaves the chart area.
//
// touch-action: pan-y opts the browser back into vertical page scroll while
// keeping horizontal pointer events for marker drag. The browser's native
// gesture-intent heuristic decides per-touch which direction wins, matching
// platform UX (Twitter, Maps, etc.). No custom JS gesture-intent code — that
// route is bug-prone for diagonal swipes.
// ---------------------------------------------------------------------------

export function attachMarkerDrag(div) {
  // pan-y: browser handles vertical scroll; horizontal pointer events still
  // reach this handler so the marker drag works. Pinch-zoom + double-tap-zoom
  // remain enabled by default (we don't need to suppress them).
  div.style.touchAction = 'pan-y';

  // Use Plotly's actual xaxis pixel offset + length for precise mapping
  const pxToX = (ev) => {
    const gd = div;
    if (!gd._fullLayout || !gd._fullLayout.xaxis) return null;
    const rect = gd.getBoundingClientRect();
    const xa = gd._fullLayout.xaxis;
    const px = ev.clientX - rect.left - xa._offset;
    if (xa._length <= 0) return null;
    const frac = Math.max(0, Math.min(1, px / xa._length));
    return xa.range[0] + frac * (xa.range[1] - xa.range[0]);
  };

  // Pending touch state: tracks finger-down position before we commit to
  // either a marker drag (horizontal intent) or surrendering to the browser
  // for vertical scroll (pan-y). Mouse/pen bypass this entirely.
  // Threshold: 6 px — small enough to feel responsive, large enough that
  // an unintentional finger jitter on tap doesn't trigger a drag. Matches
  // typical mobile gesture-intent thresholds (Plotly uses ~5, Maps ~8).
  const GESTURE_THRESHOLD_PX = 6;
  const pending = new Map(); // pointerId -> {startX, startY}

  div.addEventListener('pointerdown', (ev) => {
    // button===0: left-click (mouse), primary touch/pen contact.
    // Reject middle/right mouse buttons (button > 0).
    if (ev.button !== 0) return;
    const x = pxToX(ev);
    if (x === null) return;

    if (ev.pointerType === 'touch') {
      // Defer capture + preventDefault until we know the gesture is
      // horizontal. Calling either now would void touch-action: pan-y
      // and break vertical page scroll on touchscreens.
      pending.set(ev.pointerId, { startX: ev.clientX, startY: ev.clientY });
      return;
    }

    // Mouse / pen: claim the gesture immediately (existing behaviour).
    try {
      div.setPointerCapture(ev.pointerId);
    } catch (_) {
      /* non-fatal */
    }
    // Round 7.3: gate the seeked-listener drain in sync.js so it doesn't
    // re-issue a stashed pending while we're still scrubbing. flushPendingSeek
    // on pointerup/cancel is the only sanctioned drain during a drag.
    videoState._dragActive = true;
    const nd = Math.max(0, Math.min(1, x));
    updateMarker(nd, true, 'drag');
    ev.preventDefault();
  });

  div.addEventListener('pointermove', (ev) => {
    // Already-captured pointer (mouse, pen, or a touch we committed to):
    // drive the marker.
    if (div.hasPointerCapture(ev.pointerId)) {
      const x = pxToX(ev);
      if (x === null) return;
      const nd = Math.max(0, Math.min(1, x));
      updateMarker(nd, true, 'drag');
      return;
    }

    // Pending touch: decide intent.
    const start = pending.get(ev.pointerId);
    if (!start) return;
    const dx = ev.clientX - start.startX;
    const dy = ev.clientY - start.startY;
    const adx = Math.abs(dx);
    const ady = Math.abs(dy);
    if (adx < GESTURE_THRESHOLD_PX && ady < GESTURE_THRESHOLD_PX) return; // still waiting

    if (ady > adx) {
      // Vertical intent — abandon, let the browser scroll via pan-y.
      pending.delete(ev.pointerId);
      return;
    }

    // Horizontal intent — commit to marker drag.
    pending.delete(ev.pointerId);
    const x = pxToX(ev);
    if (x === null) return;
    try {
      div.setPointerCapture(ev.pointerId);
    } catch (_) {
      /* non-fatal */
    }
    // Round 7.3: see pointerdown comment.
    videoState._dragActive = true;
    const ndCommit = Math.max(0, Math.min(1, x));
    updateMarker(ndCommit, true, 'drag');
    ev.preventDefault();
  });

  div.addEventListener('pointerup', (ev) => {
    pending.delete(ev.pointerId);
    try {
      div.releasePointerCapture(ev.pointerId);
    } catch (_) {
      /* non-fatal */
    }
    // Round 7.3: clear gate BEFORE flushing so the seeked listener's drain
    // (if it fires before flushPendingSeek's seek even starts) doesn't see
    // a stale _dragActive=true.
    videoState._dragActive = false;
    flushPendingSeek(); // round 5: drain the drag-end stash with one seek
  });

  div.addEventListener('pointercancel', (ev) => {
    pending.delete(ev.pointerId);
    try {
      div.releasePointerCapture(ev.pointerId);
    } catch (_) {
      /* non-fatal */
    }
    hideThumbPreview();
    videoState._dragActive = false;
    flushPendingSeek(); // round 5: drain the drag-end stash with one seek
  });
}

// ---------------------------------------------------------------------------
// Link every chart's x-axis to every other chart so zoom/pan is shared.
// One plot at a time holds the `syncing` flag to prevent re-entrant fires.
// ---------------------------------------------------------------------------

export function linkXAxes(divs) {
  if (divs.length < 2) return;
  let syncing = false;
  divs.forEach((div, i) => {
    div.on('plotly_relayout', (evData) => {
      if (syncing) return;
      const update = {};
      if (evData['xaxis.range[0]'] !== undefined && evData['xaxis.range[1]'] !== undefined) {
        update['xaxis.range'] = [evData['xaxis.range[0]'], evData['xaxis.range[1]']];
      } else if (evData['xaxis.autorange']) {
        update['xaxis.autorange'] = true;
        update['yaxis.autorange'] = true;
      } else {
        return;
      }
      syncing = true;
      const others = divs.filter((_, j) => j !== i);
      Promise.all(others.map((o) => Plotly.relayout(o, update))).then(() => {
        syncing = false;
      });
    });
  });
}

// ---------------------------------------------------------------------------
// Main Plot entry — wired to the Plot button via window.plot.
// Fetches telemetry for each row×lap selection, renders one chart per
// signal, wires drag + linked axes, paints the initial marker, and hands
// off to the video module for lap-picker population.
// ---------------------------------------------------------------------------

export async function plot() {
  const selections = getSelections();
  const signals = getActiveSignals();

  if (!selections.length) {
    setStatus('Check at least one lap', true);
    return;
  }
  if (!signals.length) {
    setStatus('Select at least one signal', true);
    return;
  }

  const btn = document.getElementById('btn-plot');
  btn.disabled = true;
  setStatus('<span class="loading-spinner"></span> Loading telemetry...');

  try {
    const allData = await Promise.all(selections.map((sel) => fetchTelemetry(sel, signals)));

    const chartsDiv = document.getElementById('charts');
    chartsDiv.innerHTML = '';
    appState.plotDivs = [];
    appState.plotSignals = [];
    appState.plotTraces = [];

    for (let si = 0; si < signals.length; si++) {
      const signal = signals[si];
      const container = document.createElement('div');
      container.className = 'chart-container';

      // Header with corner overlay checkbox
      const header = document.createElement('div');
      header.className = 'chart-header';
      // Title is rendered as the Plotly y-axis label; no need to repeat it here.
      header.innerHTML = `
        <label>
          <input type="checkbox" id="corner-cb-${si}" onchange="toggleCornerOverlay(${si}, this.checked)">
          Show corners
        </label>
      `;
      container.appendChild(header);

      const plotDiv = document.createElement('div');
      container.appendChild(plotDiv);
      chartsDiv.appendChild(container);
      appState.plotDivs.push(plotDiv);
      appState.plotSignals.push(signal);

      const traces = selections.map((sel, i) => {
        const d = allData[i].data;
        const ds = downsample(d.normalizedCarPosition, d[signal]);
        return {
          x: ds.x,
          y: ds.y,
          type: 'scatter',
          mode: 'lines',
          name: sel.label,
          line: { color: sel.color, width: 1.5 },
          showlegend: true,
        };
      });
      // Remember for value interpolation at marker
      appState.plotTraces.push(
        traces.map((t) => ({ label: t.name, color: t.line.color, x: t.x, y: t.y })),
      );

      // Marker shape is always shapes[0]; corner overlays appended after when toggled
      const markerShape = {
        type: 'line',
        xref: 'x',
        yref: 'paper',
        x0: appState.markerPosition,
        x1: appState.markerPosition,
        y0: 0,
        y1: 1,
        line: { color: '#ffffff', width: 1.5, dash: 'solid' },
      };

      Plotly.newPlot(
        plotDiv,
        traces,
        {
          ...PLOTLY_LAYOUT,
          title: null,
          dragmode: false,
          shapes: [markerShape],
          xaxis: {
            ...PLOTLY_LAYOUT.xaxis,
            title: 'Track Position [-]',
            range: [0, 1],
            fixedrange: false,
          },
          yaxis: { ...PLOTLY_LAYOUT.yaxis, title: chartTitle(signal) },
        },
        { responsive: true, scrollZoom: false, doubleClick: false, displayModeBar: false },
      );

      attachMarkerDrag(plotDiv);
    }

    linkXAxes(appState.plotDivs);
    updateMarker(appState.markerPosition, true);
    const totalPts = allData.reduce((sum, d) => sum + d.count, 0);
    setStatus(`Loaded ${totalPts.toLocaleString()} points across ${selections.length} trace(s)`);
    document.dispatchEvent(new CustomEvent('plot-complete', { detail: { selections } }));
  } catch (e) {
    setStatus('Error: ' + e.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Inline-HTML handler surface. Plot button uses onclick="plot()"; corner
// overlay checkboxes use onchange="toggleCornerOverlay(si, this.checked)"
// on dynamically inserted elements.
// ---------------------------------------------------------------------------

window.plot = plot;
window.toggleCornerOverlay = toggleCornerOverlay;
