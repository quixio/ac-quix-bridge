/**
 * Track Map — 2D canvas-free Plotly rendering of the circuit outline,
 * corner badges, start/finish marker, and a movable position dot.
 *
 * State shared with the main script via the global lexical scope:
 *   reads:  trackData, trackConfig, markerPosition, trackZoom
 *   writes: trackBaseRange, window._markerTraceIdx
 *
 * Depends on (called from here, defined in the main inline script):
 *   updateMarker()  — to paint the first position dot after a fresh render
 *
 * Inline DOM handlers rely on these being global (`onZoomChange` is bound
 * in index.html via `oninput="onZoomChange(this.value)"`), so this file is
 * a plain script — not an ES module — and function declarations here are
 * automatically exposed on `window`.
 */

function renderTrackMap() {
  if (!trackData || !trackConfig) return;
  const pts = trackData.points;
  if (!pts.length) return;

  const colors = trackConfig.colors;
  const sevLabels = {
    hairpin: `Hairpin (R<${trackConfig.corner_thresholds.hairpin_max}m)`,
    tight: `Tight (${trackConfig.corner_thresholds.hairpin_max}-${trackConfig.corner_thresholds.tight_max}m)`,
    sweeper: `Sweeper (${trackConfig.corner_thresholds.tight_max}-${trackConfig.corner_thresholds.sweeper_max}m)`,
    straight: `Straight (R≥${trackConfig.corner_thresholds.sweeper_max}m)`,
  };

  const traces = [];

  // 1) Single continuous "base" line for visual connectivity (thin neutral)
  //    Ensures the track never shows gaps regardless of severity transitions.
  traces.push({
    x: pts.map((p) => p.x),
    y: pts.map((p) => -p.z),
    mode: 'lines',
    type: 'scatter',
    line: { color: '#3a3f55', width: 5, shape: 'spline', smoothing: 0.7 },
    hoverinfo: 'skip',
    showlegend: false,
  });

  // 2) Overlay colored segments per severity.
  //    Build contiguous runs bridged with the FIRST point of the next run
  //    so each colored line touches the next one pixel-perfect.
  const order = ['straight', 'sweeper', 'tight', 'hairpin'];
  const runs = {};
  for (const sev of order) runs[sev] = [];

  let curRun = null;
  for (let i = 0; i < pts.length; i++) {
    const p = pts[i];
    if (!curRun || curRun.severity !== p.severity) {
      if (curRun && curRun.x.length) runs[curRun.severity].push(curRun);
      curRun = { severity: p.severity, x: [], z: [] };
      // Prepend the previous point so this segment starts where the last ended
      if (i > 0) {
        curRun.x.push(pts[i - 1].x);
        curRun.z.push(-pts[i - 1].z);
      }
    }
    curRun.x.push(p.x);
    curRun.z.push(-p.z);
  }
  if (curRun && curRun.x.length) runs[curRun.severity].push(curRun);

  const legendShown = { hairpin: false, tight: false, sweeper: false, straight: false };
  for (const sev of order) {
    for (const run of runs[sev]) {
      traces.push({
        x: run.x,
        y: run.z,
        mode: 'lines',
        type: 'scatter',
        line: { color: colors[sev], width: 4, shape: 'spline', smoothing: 0.7 },
        hoverinfo: 'skip',
        name: sevLabels[sev],
        legendgroup: sev,
        showlegend: !legendShown[sev],
      });
      legendShown[sev] = true;
    }
  }

  // 3) Corner number badges
  trackData.corners.forEach((c) => {
    traces.push({
      x: [c.mid_x],
      y: [-c.mid_z],
      mode: 'markers+text',
      type: 'scatter',
      marker: { color: colors[c.severity], size: 16, line: { color: '#0f1117', width: 1.5 } },
      text: [c.label],
      textfont: { color: '#fff', size: 9, family: 'monospace' },
      hoverinfo: 'text',
      hovertext: `${c.label}: ${c.name || ''} (R=${c.min_radius_m}m)`,
      showlegend: false,
    });
  });

  // 4) Start/Finish
  traces.push({
    x: [pts[0].x],
    y: [-pts[0].z],
    mode: 'markers',
    type: 'scatter',
    marker: {
      color: colors.start_finish,
      size: 11,
      symbol: 'square',
      line: { color: '#000', width: 1 },
    },
    hoverinfo: 'text',
    hovertext: 'Start / Finish',
    name: 'Start/Finish',
    showlegend: true,
  });

  // 5) Moving position dot — distinct color from the marker line so the
  //    cream-on-plot line and the red-on-track dot read as separate cues.
  traces.push({
    x: [pts[0].x],
    y: [-pts[0].z],
    mode: 'markers',
    type: 'scatter',
    marker: {
      color: colors.track_dot || colors.marker,
      size: 13,
      line: { color: '#fff', width: 2 },
    },
    hoverinfo: 'text',
    hovertext: 'Position',
    name: 'Current',
    showlegend: false,
  });
  window._markerTraceIdx = traces.length - 1;

  // Compute and cache base ranges for zoom math.
  // Use a square bounding box centered on the track so scaleanchor fits
  // the whole track regardless of container aspect.
  const xs = pts.map((p) => p.x),
    zs = pts.map((p) => -p.z);
  const xMin = Math.min(...xs),
    xMax = Math.max(...xs);
  const zMin = Math.min(...zs),
    zMax = Math.max(...zs);
  const cx = (xMin + xMax) / 2;
  const cz = (zMin + zMax) / 2;
  const half = (Math.max(xMax - xMin, zMax - zMin) / 2) * 1.08; // 8% padding
  trackBaseRange = {
    xMin: cx - half,
    xMax: cx + half,
    zMin: cz - half,
    zMax: cz + half,
  };

  // Layout: legend on top, no grid, no axis titles
  const layout = {
    paper_bgcolor: '#1a1d27',
    plot_bgcolor: '#1a1d27',
    font: { color: '#e2e8f0', size: 9 },
    margin: { t: 40, r: 4, b: 4, l: 4 },
    showlegend: true,
    legend: {
      orientation: 'h',
      x: 0.5,
      xanchor: 'center',
      y: 1.08,
      yanchor: 'bottom',
      font: { size: 8 },
      bgcolor: 'rgba(0,0,0,0)',
      traceorder: 'normal',
    },
    dragmode: false,
    xaxis: {
      visible: false,
      showgrid: false,
      zeroline: false,
      showticklabels: false,
      scaleanchor: 'y',
      scaleratio: 1,
      range: [trackBaseRange.xMin, trackBaseRange.xMax],
      fixedrange: true,
    },
    yaxis: {
      visible: false,
      showgrid: false,
      zeroline: false,
      showticklabels: false,
      range: [trackBaseRange.zMin, trackBaseRange.zMax],
      fixedrange: true,
    },
  };

  const div = document.getElementById('track-map');
  Plotly.newPlot(div, traces, layout, {
    responsive: true,
    displayModeBar: false,
    scrollZoom: false,
    doubleClick: false,
    staticPlot: false,
  });
  updateMarker(markerPosition, true);
  applyZoom();

  // Populate corner legend table
  const legendEl = document.getElementById('corner-legend');
  if (legendEl && trackData.corners.length) {
    legendEl.innerHTML = trackData.corners
      .map((c) => {
        const dotColor = colors[c.severity] || '#888';
        return (
          `<div class="legend-row">` +
          `<span class="legend-dot" style="background:${dotColor}"></span>` +
          `<span class="legend-label">${c.label}</span>` +
          `<span class="legend-name">${c.name || ''}</span>` +
          `</div>`
        );
      })
      .join('');
  }

  // §11 tabbed-float: progressive legend hiding based on map pane width.
  // Install once per renderTrackMap call; idempotent thanks to the
  // _installedMapResponsiveness sentinel.
  installMapResponsiveness(div, legendEl);
}

/**
 * §11 tabbed-float spec: progressively hide legends as the map pane
 * shrinks, so the circuit stays readable on cramped viewports and inside
 * small floating windows.
 *
 * Tiers (measured on the map pane's rendered inner box, i.e. the #topbar-map
 * body element — NOT the Plotly div itself, to avoid observer feedback loops
 * when Plotly mutates its own inner layout):
 *
 *   T1 full     (w >= 520 px)       everything visible
 *   T2 compact  (320 <= w < 520)    Plotly severity legend hidden
 *   T3 minimal  (240 <= w < 320)    + corner-legend side panel hidden
 *
 * Hard floor (w >= 240 px) is enforced by CSS `min-width: 240px` on
 * #topbar-map in styles.css.
 *
 * We observe the pane's parent (#topbar-map's collapsible body) rather than
 * #track-map because Plotly writes into #track-map during relayout — an
 * observer on that element would fire recursively.
 */
let _mapResponsivenessInstalled = false;
function installMapResponsiveness(mapDiv, legendDiv) {
  if (_mapResponsivenessInstalled) return;
  if (typeof ResizeObserver === 'undefined') return; // legacy browser fallback: skip
  const paneBody = mapDiv.closest('[data-collapsible-body]') || mapDiv.parentElement;
  if (!paneBody) return;

  let lastTier = null;
  const apply = (w) => {
    const tier = w >= 520 ? 'full' : w >= 320 ? 'compact' : 'minimal';
    if (tier === lastTier) return;
    lastTier = tier;
    // Plotly severity legend — toggle via relayout. Safe against missing
    // data (if newPlot hasn't run yet Plotly will throw; guard with try).
    try {
      if (mapDiv.data) {
        Plotly.relayout(mapDiv, { showlegend: tier === 'full' });
      }
    } catch (_) {
      /* non-fatal — Plotly wasn't ready */
    }
    // Corner-legend side panel — drive via inline display because Tailwind
    // JIT can't see dynamically toggled class strings.
    //
    // Nitpicker R1: in floating mode a CSS rule
    // `body[data-video-mode='floating'] #corner-legend { display: none }`
    // takes precedence over the "full/compact" tiers (which would otherwise
    // keep the corner-legend visible and eat 160px of a 478px pane). Clear
    // our inline style when floating so the CSS cascade wins — `display: ''`
    // removes the inline property, letting the stylesheet rule apply.
    if (legendDiv) {
      const floating = document.body.dataset.videoMode === 'floating';
      if (floating) {
        legendDiv.style.display = '';
      } else {
        legendDiv.style.display = tier === 'minimal' ? 'none' : '';
      }
    }
  };

  const obs = new ResizeObserver((entries) => {
    const w = entries[0].contentRect.width;
    apply(w);
  });
  obs.observe(paneBody);
  // Apply immediately with current width so the first paint is correct.
  apply(paneBody.getBoundingClientRect().width);
  _mapResponsivenessInstalled = true;
}

function onZoomChange(v) {
  trackZoom = parseFloat(v) || 1;
  document.getElementById('track-zoom-val').textContent = trackZoom.toFixed(1) + 'x';
  applyZoom();
}

function applyZoom() {
  if (!trackBaseRange) return;
  const div = document.getElementById('track-map');
  if (!div || !div.layout) return;

  if (trackZoom <= 1.02) {
    Plotly.relayout(div, {
      'xaxis.range': [trackBaseRange.xMin, trackBaseRange.xMax],
      'yaxis.range': [trackBaseRange.zMin, trackBaseRange.zMax],
    });
    return;
  }

  const p = trackPointAtNorm(markerPosition);
  const cx = p ? p.x : (trackBaseRange.xMin + trackBaseRange.xMax) / 2;
  const cz = p ? -p.z : (trackBaseRange.zMin + trackBaseRange.zMax) / 2;
  const fullW = trackBaseRange.xMax - trackBaseRange.xMin;
  const fullH = trackBaseRange.zMax - trackBaseRange.zMin;
  const w = fullW / trackZoom;
  const h = fullH / trackZoom;
  Plotly.relayout(div, {
    'xaxis.range': [cx - w / 2, cx + w / 2],
    'yaxis.range': [cz - h / 2, cz + h / 2],
  });
}

function trackPointAtNorm(nd) {
  if (!trackData?.points?.length) return null;
  const pts = trackData.points;
  nd = Math.max(0, Math.min(1, nd));
  // Binary search on normalizedDistance (already sorted)
  let lo = 0,
    hi = pts.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (pts[mid].normalizedDistance < nd) lo = mid + 1;
    else hi = mid;
  }
  return pts[lo];
}
