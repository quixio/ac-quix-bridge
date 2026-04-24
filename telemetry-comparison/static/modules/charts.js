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

import { appState, PLOTLY_LAYOUT } from './state.js';
import { downsample, fetchTelemetry } from './data.js';
import { updateMarker } from './sync.js';
import { getSelections, getActiveSignals, chartTitle } from './selections.js';

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
// Marker drag — mousedown anywhere on the plot area starts dragging; window
// mousemove/mouseup complete the gesture so a drag that leaves the plot
// doesn't get stuck.
// ---------------------------------------------------------------------------

export function attachMarkerDrag(div) {
  let dragging = false;

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

  div.addEventListener('mousedown', (ev) => {
    if (ev.button !== 0) return;
    const x = pxToX(ev);
    if (x === null) return;
    dragging = true;
    updateMarker(Math.max(0, Math.min(1, x)), true, 'drag');
    ev.preventDefault();
  });

  window.addEventListener('mousemove', (ev) => {
    if (!dragging) return;
    const x = pxToX(ev);
    if (x === null) return;
    updateMarker(Math.max(0, Math.min(1, x)), true, 'drag');
  });

  window.addEventListener('mouseup', () => {
    dragging = false;
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
      header.innerHTML = `
        <span>${chartTitle(signal)}</span>
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
        { responsive: true, scrollZoom: false },
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
