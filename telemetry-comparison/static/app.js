// 16 distinct colors — each trace (row+lap) gets a unique one
const TRACE_COLORS = [
  '#4f8ef7', '#f59e0b', '#34d399', '#f87171', '#a78bfa',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#8b5cf6',
  '#14b8a6', '#e879f9', '#fb923c', '#38bdf8', '#a3e635',
  '#fbbf24'
];
let globalTraceIdx = 0;

const ROW_COLORS = TRACE_COLORS;
const PART_COLS = ['environment', 'test_rig', 'experiment', 'driver', 'track', 'carModel', 'session_id'];
const PART_LABELS = {
  environment: 'Env', test_rig: 'Rig', experiment: 'Experiment',
  driver: 'Driver', track: 'Track', carModel: 'Car', session_id: 'Session',
};

const PLOTLY_LAYOUT = {
  paper_bgcolor: '#1a1d27',
  plot_bgcolor: '#1a1d27',
  font: { color: '#e2e8f0', size: 11 },
  legend: { orientation: 'v', x: 1.02, y: 1, font: { size: 8 } },
  margin: { t: 10, r: 60, b: 40, l: 55 },
  height: 240,
  xaxis: { color: '#8892a4', gridcolor: '#2d3047', zerolinecolor: '#2d3047' },
  yaxis: { color: '#8892a4', gridcolor: '#2d3047', zerolinecolor: '#2d3047', autorange: true },
};

let sessions = [];   // loaded once on tab open, filtered client-side for dropdowns
let channels = {};
let rowCount = 0;
let plotDivs = [];

// --- Track state ---
let trackData = null;      // { points: [{x,z,normalizedDistance,radius_m,severity,...}], corners: [...] }
let trackConfig = null;    // { corner_thresholds, colors, ... }
let markerPosition = 0;    // current normalizedCarPosition (0..1), persists across re-plots
let plotSignals = [];      // list of signal keys currently displayed (aligned with plotDivs)
let plotTraces = [];       // per plot: array of {label, color, x, y} used for value lookup
let trackBaseRange = null; // { xMin, xMax, zMin, zMax } of full track (for zoom math)
let trackZoom = 1;         // current zoom factor (1 = fit whole track)

// --- Video sync state (populated after Plot when a lap with a sidecar exists) ---
let videoState = {
  element: null,        // <video> reference (set once at init)
  laps: [],             // currently selectable laps (subset of plot selections)
  currentLapIdx: -1,    // index in videoState.laps, -1 = none loaded
  currentLoadToken: 0,  // monotonic, used to ignore stale async loads
  frames: null,         // sorted-by-t_ms array of {t_ms, normPos} for the loaded lap
  framesByNd: null,
  isPlaying: false,     // true between 'play' and 'pause'/'ended' events
  blobUrl: null,        // object URL for fully-buffered MP4
};

// Video-frame-accurate sync: use requestVideoFrameCallback when available.
// It fires once per displayed frame with the exact mediaTime of that frame,
// eliminating drift from rAF polling + browser decode lag.
const HAS_RVFC = typeof HTMLVideoElement !== 'undefined'
  && 'requestVideoFrameCallback' in HTMLVideoElement.prototype;

// Fallback: if requestVideoFrameCallback is absent, poll via rAF at ~30Hz.
const VIDEO_RAF_INTERVAL_MS = 1000 / 30;

const MAX_READOUTS = 6;

const DEFAULT_ACTIVE = new Set(['speedKmh', 'gas', 'brake', 'rpms']);
const CAT_ORDER = ['Inputs', 'Motion', 'Engine', 'Tyres', 'Suspension & Brakes', 'Environment', 'Car State', 'Session'];

// ---------------------------------------------------------------------------
// Data — loaded once at startup
// ---------------------------------------------------------------------------

async function fetchSessions(filters) {
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
    } catch { /* non-JSON response */ }
    const err = new Error(detail || `HTTP ${res.status}`);
    err.status = res.status;
    err.detail = detail;
    throw err;
  }
  const json = await res.json();
  return json.sessions || [];
}

async function fetchTrack() {
  try {
    const [tRes, cRes] = await Promise.all([
      fetch('/api/track'),
      fetch('/api/track/config'),
    ]);
    trackData = await tRes.json();
    trackConfig = await cRes.json();
    renderTrackMap();
  } catch (e) {
    console.warn('Track data unavailable:', e);
  }
}

function updateMarker(nd, forceTrack, source) {
  markerPosition = nd;
  // Video sync — only marker→video; video→marker callers pass source='video'.
  syncVideoFromMarker(nd, source);

  // Update track dot
  if (forceTrack || trackData) {
    const p = trackPointAtNorm(nd);
    if (p && window._markerTraceIdx !== undefined) {
      const div = document.getElementById('track-map');
      if (div && div.data) {
        Plotly.restyle(div, { x: [[p.x]], y: [[-p.z]] }, [window._markerTraceIdx]);
        // Re-center zoom window on the dot ONLY when zoomed in
        if (trackZoom > 1.02) applyZoom();
      }
    }
  }

  // Update marker line + per-trace value annotations (up to 6 per plot) on every plot
  for (let i = 0; i < plotDivs.length; i++) {
    const div = plotDivs[i];
    if (!div.layout) continue;

    const traces = plotTraces[i] || [];
    const cornerAnn = div._cornerAnnotations || [];
    const valueAnn = [];
    const shown = Math.min(MAX_TRACE_ANNOTATIONS, traces.length);

    // Stack annotations vertically in a fixed column next to the marker line
    // so they never overlap regardless of where the trace values fall.
    // Selected (highlighted) annotation renders last so it draws on top.
    const ROW_H = 18; // px between stacked labels
    let hlAnn = null;
    for (let k = 0; k < shown; k++) {
      const t = traces[k];
      const v = interpolateAt(t.x, t.y, nd);
      if (v === null || !isFinite(v)) continue;
      const valStr = (Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2));
      const isHL = _highlightedLabel && t.label === _highlightedLabel;
      const ann = {
        xref: 'x', yref: 'paper',
        x: nd, y: 1,
        text: isHL ? `<b>${valStr}</b>` : valStr,
        showarrow: false,
        xanchor: 'left',
        yanchor: 'top',
        xshift: 6,
        yshift: -(k * ROW_H) - 2,
        font: { color: t.color, size: 11, family: 'monospace' },
        bgcolor: 'rgba(15,17,23,0.9)',
        bordercolor: t.color,
        borderwidth: isHL ? 2 : 1,
        borderpad: 2,
        opacity: 1,
      };
      if (isHL) { hlAnn = ann; } else { valueAnn.push(ann); }
    }
    if (hlAnn) valueAnn.push(hlAnn);

    if (traces.length > MAX_TRACE_ANNOTATIONS) {
      valueAnn.push({
        xref: 'x', yref: 'paper',
        x: nd, y: 1,
        text: `+${traces.length - MAX_TRACE_ANNOTATIONS}`,
        showarrow: false,
        xanchor: 'left', yanchor: 'top',
        xshift: 6,
        yshift: -(shown * ROW_H) - 2,
        font: { color: '#8892a4', size: 9, style: 'italic' },
        bgcolor: 'rgba(15,17,23,0.7)',
        borderpad: 1,
      });
    }

    Plotly.relayout(div, {
      'shapes[0].x0': nd,
      'shapes[0].x1': nd,
      annotations: cornerAnn.concat(valueAnn),
    });
  }

  updateReadout();
}

function updateReadout() {
  const el = document.getElementById('readout-pos-text');
  if (!el) return;

  const trackPt = trackPointAtNorm(markerPosition);
  el.textContent = trackPt
    ? `${(markerPosition * 100).toFixed(1)}% @ ${trackPt.distance_m.toFixed(0)}m`
    : `${(markerPosition * 100).toFixed(1)}%`;
}

const MAX_TRACE_ANNOTATIONS = 6;

function interpolateAt(xArr, yArr, xTarget) {
  if (!xArr || !xArr.length) return null;
  if (xTarget <= xArr[0]) return yArr[0];
  if (xTarget >= xArr[xArr.length - 1]) return yArr[yArr.length - 1];
  // Binary search
  let lo = 0, hi = xArr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (xArr[mid] <= xTarget) lo = mid;
    else hi = mid;
  }
  const x0 = xArr[lo], x1 = xArr[hi];
  const y0 = yArr[lo], y1 = yArr[hi];
  if (x1 === x0) return y0;
  return y0 + (y1 - y0) * (xTarget - x0) / (x1 - x0);
}

// Corner overlay shapes for a plot, based on config colors + enabled checkbox
function buildCornerShapes() {
  if (!trackData?.corners || !trackConfig) return [];
  const colors = trackConfig.colors;
  return trackData.corners.map(c => ({
    type: 'rect',
    xref: 'x', yref: 'paper',
    x0: c.start_norm, x1: c.end_norm,
    y0: 0, y1: 1,
    fillcolor: colors[c.severity],
    opacity: 0.18,
    line: { width: 0 },
    layer: 'below',
  }));
}

function buildCornerAnnotations() {
  if (!trackData?.corners) return [];
  return trackData.corners.map(c => ({
    xref: 'x', yref: 'paper',
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

function toggleCornerOverlay(plotIdx, enabled) {
  const div = plotDivs[plotIdx];
  if (!div) return;
  // Preserve the marker shape (always shapes[0])
  const base = [div.layout.shapes[0]];
  const shapes = enabled ? base.concat(buildCornerShapes()) : base;
  // Store corner annotations separately so updateMarker can preserve them
  div._cornerAnnotations = enabled ? buildCornerAnnotations() : [];
  Plotly.relayout(div, { shapes });
  // Refresh so value annotations + corner labels coexist
  updateMarker(markerPosition, false);
}

// ---------------------------------------------------------------------------
// Client-side partition filtering over the `sessions` array loaded once at
// tab open. Instant (pure array work, no network).
// ---------------------------------------------------------------------------

function getDistinctValues(column, upstreamFilters) {
  let filtered = sessions;
  for (const [col, val] of Object.entries(upstreamFilters)) {
    if (val) filtered = filtered.filter(s => String(s[col]) === String(val));
  }
  const vals = [...new Set(filtered.map(s => s[column]))].filter(v => v !== undefined);
  vals.sort();
  return vals;
}

// ---------------------------------------------------------------------------
// Row management — cascading dropdowns (client-side)
// ---------------------------------------------------------------------------

function getRowFilters(idx) {
  const filters = {};
  for (const col of PART_COLS) {
    const sel = document.getElementById(`${col}-${idx}`);
    if (sel) filters[col] = sel.value;
  }
  return filters;
}

function getPreviousRowFilters() {
  const rows = document.querySelectorAll('.selection-row');
  if (!rows.length) return {};
  const last = rows[rows.length - 1];
  return getRowFilters(parseInt(last.dataset.rowIdx));
}

function addRow(defaults) {
  const idx = rowCount++;
  const color = ROW_COLORS[idx % ROW_COLORS.length];
  const prev = defaults || getPreviousRowFilters();

  const row = document.createElement('div');
  row.className = 'selection-row';
  row.id = 'row-' + idx;
  row.dataset.rowIdx = idx;

  let html = '';
  for (const col of PART_COLS) {
    html += `
      <div class="part-group">
        <span class="part-label">${PART_LABELS[col]}</span>
        <select class="part-select" id="${col}-${idx}"
                onchange="onPartChange(${idx}, '${col}')">
          <option value="">...</option>
        </select>
      </div>`;
  }
  html += `
    <div class="part-group">
      <span class="part-label">Laps</span>
      <div class="lap-checkboxes" id="laps-${idx}">
        <span class="lap-info">...</span>
      </div>
    </div>
    <button class="btn btn-xs btn-danger" onclick="removeRow(${idx})">x</button>`;

  row.innerHTML = html;
  document.getElementById('selections').appendChild(row);
  populateDropdowns(idx, 0, prev);
}

function removeRow(idx) {
  const el = document.getElementById('row-' + idx);
  if (el) el.remove();
}

function populateDropdowns(rowIdx, fromColIdx, defaults) {
  for (let ci = fromColIdx; ci < PART_COLS.length; ci++) {
    const col = PART_COLS[ci];
    const sel = document.getElementById(`${col}-${rowIdx}`);
    if (!sel) continue;

    // Build upstream filters from columns before this one
    const upstream = {};
    for (let j = 0; j < ci; j++) {
      const c = PART_COLS[j];
      const s = document.getElementById(`${c}-${rowIdx}`);
      if (s?.value) upstream[c] = s.value;
    }

    const values = getDistinctValues(col, upstream);

    sel.innerHTML = values.length === 1 ? '' : '<option value="">...</option>';
    for (const v of values) {
      const display = col === 'session_id'
        ? String(v).substring(0, 19).replace('T', ' ')
        : (v || '(empty)');
      sel.innerHTML += `<option value="${v}">${display}</option>`;
    }

    // Auto-select: default if it matches, otherwise the first available value
    // (matches the Test Manager pattern — always present a complete selection).
    const def = defaults?.[col];
    if (def && values.map(String).includes(String(def))) {
      sel.value = def;
    } else if (values.length > 0) {
      sel.value = values[0];
    } else {
      for (let k = ci + 1; k < PART_COLS.length; k++) {
        const ds = document.getElementById(`${PART_COLS[k]}-${rowIdx}`);
        if (ds) ds.innerHTML = '<option value="">...</option>';
      }
      document.getElementById(`laps-${rowIdx}`).innerHTML = '<span class="lap-info">...</span>';
      return;
    }
  }
  loadLaps(rowIdx);
}

function onPartChange(rowIdx, changedCol) {
  const colIdx = PART_COLS.indexOf(changedCol);
  populateDropdowns(rowIdx, colIdx + 1, null);
}

function loadLaps(rowIdx) {
  const container = document.getElementById(`laps-${rowIdx}`);
  const filters = getRowFilters(rowIdx);

  if (!PART_COLS.every(c => filters[c])) {
    container.innerHTML = '<span class="lap-info">...</span>';
    return;
  }

  // Laps are baked into each session object by /api/sessions — no extra
  // HTTP call. Find the matching pre-loaded session and render.
  const session = sessions.find(s =>
    PART_COLS.every(c => String(s[c]) === String(filters[c])),
  );
  const laps = session?.laps || [];

  if (!laps.length) {
    container.innerHTML = '<span class="lap-info">no laps</span>';
    return;
  }

  container.innerHTML = `
    <button class="btn btn-xs btn-outline" onclick="toggleAllLaps(${rowIdx})">all</button>
  ` + laps.map(lap => `
    <label class="lap-cb" onclick="event.preventDefault(); this.classList.toggle('checked'); this.querySelector('input').checked = this.classList.contains('checked');">
      <input type="checkbox" value="${lap}">
      L${lap}
    </label>
  `).join('');
}

function toggleAllLaps(idx) {
  const labels = document.querySelectorAll(`#laps-${idx} .lap-cb`);
  const anyUnchecked = Array.from(labels).some(l => !l.classList.contains('checked'));
  labels.forEach(l => {
    l.classList.toggle('checked', anyUnchecked);
    l.querySelector('input').checked = anyUnchecked;
  });
}

// ---------------------------------------------------------------------------
// Gather selections
// ---------------------------------------------------------------------------

function getSelections() {
  const result = [];
  let colorIdx = 0;

  // Collect all row session_ids first to detect multi-session
  const rows = document.querySelectorAll('.selection-row');
  const sessionIds = [];
  rows.forEach(row => {
    const filters = getRowFilters(parseInt(row.dataset.rowIdx));
    sessionIds.push(filters.session_id || '');
  });
  const uniqueSessions = [...new Set(sessionIds)];
  const multiSession = uniqueSessions.length > 1;

  rows.forEach((row, rowIdx) => {
    const filters = getRowFilters(parseInt(row.dataset.rowIdx));
    const sIdx = multiSession ? uniqueSessions.indexOf(sessionIds[rowIdx]) + 1 : -1;

    const checked = row.querySelectorAll('.lap-cb input:checked');
    checked.forEach(cb => {
      const lap = parseInt(cb.value);
      const label = multiSession
        ? `S${sIdx}-L${lap}`
        : `L${lap}`;
      result.push({
        key: { ...filters },
        lap,
        color: TRACE_COLORS[colorIdx++ % TRACE_COLORS.length],
        label,
      });
    });
  });
  return result;
}

// ---------------------------------------------------------------------------
// Channels / Signals
// ---------------------------------------------------------------------------

function getActiveSignals() {
  return Array.from(document.querySelectorAll('.chip.active')).map(c => c.dataset.signal);
}

function chartTitle(col) {
  const ch = channels[col];
  return ch ? `${ch.label} ${ch.unit}` : col;
}

async function loadChannels() {
  const res = await fetch('/api/channels');
  channels = await res.json();
  renderChannelChips();
}

const MAX_VISIBLE = 8;

function renderChannelChips() {
  const container = document.getElementById('signal-container');
  const groups = {};
  for (const [col, meta] of Object.entries(channels)) {
    const cat = meta.cat || 'Other';
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(col);
  }

  let html = '';
  for (const cat of CAT_ORDER) {
    const cols = groups[cat];
    if (!cols) continue;
    const collapsed = cols.length > MAX_VISIBLE;
    const catId = cat.replace(/[^a-zA-Z]/g, '');

    html += `<div class="cat-section"><div class="cat-header">`;
    html += `<span class="cat-label">${cat}</span>`;
    if (collapsed) html += `<button class="cat-toggle" onclick="toggleCat('${catId}', this)">show all ${cols.length}</button>`;
    html += `</div><div class="signal-chips" id="cat-${catId}">`;
    for (let i = 0; i < cols.length; i++) {
      const col = cols[i];
      const m = channels[col];
      const active = DEFAULT_ACTIVE.has(col) ? ' active' : '';
      const hidden = collapsed && i >= MAX_VISIBLE ? ' style="display:none" data-extra' : '';
      html += `<span class="chip${active}" data-signal="${col}"${hidden}>${m.label}</span>`;
    }
    html += '</div></div>';
  }
  container.innerHTML = html;
  container.addEventListener('click', e => {
    if (e.target.classList.contains('chip')) e.target.classList.toggle('active');
  });
}

function toggleCat(catId, btn) {
  const chips = document.querySelectorAll(`#cat-${catId} [data-extra]`);
  const showing = chips[0]?.style.display !== 'none';
  chips.forEach(c => c.style.display = showing ? 'none' : '');
  btn.textContent = showing ? 'show all' : 'show less';
}

// ---------------------------------------------------------------------------
// Plot
// ---------------------------------------------------------------------------

async function plot() {
  const selections = getSelections();
  const signals = getActiveSignals();

  if (!selections.length) { setStatus('Check at least one lap', true); return; }
  if (!signals.length) { setStatus('Select at least one signal', true); return; }

  const btn = document.getElementById('btn-plot');
  btn.disabled = true;
  setStatus('<span class="loading-spinner"></span> Loading telemetry...');

  try {
    const allData = await Promise.all(selections.map(sel => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(sel.key)) {
        if (v) p.set(k, v);
      }
      p.set('lap', sel.lap);
      p.set('signals', signals.join(','));
      return fetch('/api/telemetry?' + p)
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); });
    }));

    const chartsDiv = document.getElementById('charts');
    chartsDiv.innerHTML = '';
    plotDivs = [];
    plotSignals = [];
    plotTraces = [];

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
      plotDivs.push(plotDiv);
      plotSignals.push(signal);

      const traces = selections.map((sel, i) => {
        const d = allData[i].data;
        const ds = downsample(d.normalizedCarPosition, d[signal]);
        return {
          x: ds.x, y: ds.y,
          type: 'scatter', mode: 'lines',
          name: sel.label,
          line: { color: sel.color, width: 1.5 },
          showlegend: true,
        };
      });
      // Remember for value interpolation at marker
      plotTraces.push(traces.map(t => ({ label: t.name, color: t.line.color, x: t.x, y: t.y })));

      // Marker shape is always shapes[0]; corner overlays appended after when toggled
      const markerShape = {
        type: 'line',
        xref: 'x', yref: 'paper',
        x0: markerPosition, x1: markerPosition,
        y0: 0, y1: 1,
        line: { color: '#ffffff', width: 1.5, dash: 'solid' },
      };

      Plotly.newPlot(plotDiv, traces, {
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
      }, { responsive: true, scrollZoom: false });

      attachMarkerDrag(plotDiv);
    }

    linkXAxes(plotDivs);
    updateMarker(markerPosition, true);
    const totalPts = allData.reduce((sum, d) => sum + d.count, 0);
    setStatus(`Loaded ${totalPts.toLocaleString()} points across ${selections.length} trace(s)`);
    populateVideoLapPicker(selections);
  } catch (e) {
    setStatus('Error: ' + e.message, true);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Neon highlight for video-selected lap trace
// ---------------------------------------------------------------------------

let _highlightedLabel = null;

function highlightVideoLapTrace(label) {
  _highlightedLabel = label;
  if (!plotDivs.length || !plotTraces.length) return;
  const matchIdx = plotTraces[0]?.findIndex(t => t.label === label) ?? -1;
  const selColor = plotTraces[0]?.[matchIdx]?.color;

  plotDivs.forEach((div, pi) => {
    const traces = plotTraces[pi];
    if (!traces || !div.data) return;

    // 1) Remove old halos FIRST so trace indices are clean
    const haloIdxs = [];
    div.data.forEach((d, i) => { if (d.name === '_halo') haloIdxs.push(i); });
    for (let i = haloIdxs.length - 1; i >= 0; i--) Plotly.deleteTraces(div, haloIdxs[i]);

    // 2) Width: selected bolder, ALL others back to normal
    traces.forEach((t, i) => {
      Plotly.restyle(div, { 'line.width': t.label === label ? 3 : 1.5, opacity: t.label === label ? 1 : 0.7 }, [i]);
    });
  });

  // 3) DOM: move selected trace SVG to front + highlight legend + annotations
  // Only target chart plots (plotDivs), not the track map
  setTimeout(() => {
    plotDivs.forEach(plot => {
      // Bring selected trace to front
      const layer = plot.querySelector('.scatterlayer');
      if (layer) {
        layer.querySelectorAll('.trace').forEach(tr => {
          const path = tr.querySelector('path.js-line');
          if (path) {
            const w = parseFloat(path.getAttribute('stroke-width') || '1');
            if (w >= 2.5) layer.appendChild(tr);
          }
        });
      }

      // Legend highlight
      const legendEntries = plot.querySelectorAll('.legend .traces');
      legendEntries.forEach((entry, i) => {
        const isMatch = i === matchIdx;
        entry.querySelectorAll('text').forEach(t => {
          t.style.fontWeight = isMatch ? 'bold' : '';
          t.style.opacity = isMatch ? '1' : '0.5';
          t.style.filter = isMatch ? `drop-shadow(0 0 6px ${selColor || '#fff'})` : '';
        });
        entry.querySelectorAll('path, line, rect').forEach(l => {
          l.style.opacity = isMatch ? '1' : '0.5';
          l.style.filter = isMatch ? `drop-shadow(0 0 6px ${selColor || '#fff'})` : '';
        });
      });

      // Annotation highlight handled via Plotly layout in updateMarker()
      // Force a marker update to re-render annotations with highlight
      updateMarker(markerPosition, false);
    });
  }, 150);
}

function _highlightAnnotations(plot, matchIdx, selColor) {
  const annotations = plot.querySelectorAll('.annotation');
  annotations.forEach((ann, i) => {
    const bg = ann.querySelector('rect');
    const txt = ann.querySelector('text');
    if (!bg || !txt) return;
    const isMatch = i === matchIdx;
    if (isMatch) {
      txt.style.fontWeight = 'bold';
      txt.style.fontSize = '13px';
      txt.style.opacity = '1';
      bg.style.opacity = '1';
      bg.style.filter = selColor ? `drop-shadow(0 0 4px ${selColor})` : '';
    } else {
      txt.style.fontWeight = '';
      txt.style.fontSize = '';
      txt.style.opacity = '0.5';
      bg.style.opacity = '0.5';
      bg.style.filter = '';
    }
  });
}

function clearTraceHighlight() {
  _highlightedLabel = null;
  if (!plotDivs.length || !plotTraces.length) return;
  plotDivs.forEach((div, pi) => {
    const traces = plotTraces[pi];
    if (!traces || !div.data) return;
    traces.forEach((t, i) => {
      Plotly.restyle(div, { 'line.width': 1.5, opacity: 1 }, [i]);
    });
    // Remove halo traces
    const haloIdxs = [];
    div.data.forEach((d, i) => { if (d.name === '_halo') haloIdxs.push(i); });
    for (let i = haloIdxs.length - 1; i >= 0; i--) Plotly.deleteTraces(div, haloIdxs[i]);
  });
  // Reset legend styles — only chart plots, not track map
  plotDivs.forEach(plot => {
    plot.querySelectorAll('.legend .traces').forEach(entry => {
      entry.querySelectorAll('text, path, line, rect').forEach(el => {
        el.style.fontWeight = '';
        el.style.opacity = '';
        el.style.filter = '';
      });
    });
  });
  // Refresh annotations without highlight
  updateMarker(markerPosition, false);
}


// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function downsample(x, y, maxPoints = 1500) {
  if (!x || x.length <= maxPoints) return { x, y };
  const step = x.length / maxPoints;
  const nx = [], ny = [];
  for (let i = 0; i < maxPoints; i++) {
    const idx = Math.round(i * step);
    nx.push(x[idx]);
    ny.push(y[idx]);
  }
  return { x: nx, y: ny };
}

function toggleTopbarPanel(panelId, btn) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  panel.classList.toggle('collapsed');
  btn.textContent = panel.classList.contains('collapsed') ? '+' : '-';
  const topbar = document.getElementById('topbar');
  const allCollapsed = topbar.querySelectorAll('.topbar-panel:not(.collapsed)').length === 0;
  topbar.style.height = allCollapsed ? 'auto' : '';
  requestAnimationFrame(() => {
    const h = topbar.offsetHeight;
    document.body.style.paddingTop = h + 'px';
    const strip = document.querySelector('.readout-strip');
    if (strip) strip.style.top = h + 'px';
  });
}

function togglePanel(panelId, btn) {
  const p = document.getElementById(panelId);
  if (!p) return;
  p.classList.toggle('collapsed');
  btn.textContent = p.classList.contains('collapsed') ? '+' : '-';
}

function attachMarkerDrag(div) {
  // Drag the vertical marker line via mouse: mousedown on the plot
  // area anywhere, then mousemove updates position while button held.
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

  window.addEventListener('mouseup', () => { dragging = false; });
}

function linkXAxes(divs) {
  if (divs.length < 2) return;
  let syncing = false;
  divs.forEach((div, i) => {
    div.on('plotly_relayout', evData => {
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
      Promise.all(others.map(o => Plotly.relayout(o, update))).then(() => { syncing = false; });
    });
  });
}

function setStatus(msg, isError = false) {
  const el = document.getElementById('status');
  el.innerHTML = msg;
  el.className = 'status-bar' + (isError ? ' error' : '');
}

// ---------------------------------------------------------------------------
// Video sync (MP4 + sidecar) — see docs/video-sync-design.md
//
// Mode-switched bidirectional sync, no feedback-loop hack:
//   - Video PLAYING  → video.timeupdate drives the marker (and red dot).
//   - Video PAUSED   → user dragging the marker seeks the video.
//
// Lookups: sidecar JSON has frames=[{idx,t_ms,wall_ms,normPos}, ...] sampled
// at SIDECAR_SAMPLE_HZ. Sorted by t_ms. We interpolate linearly for both
// directions because normPos and t_ms are both monotonic across a normal lap.
// ---------------------------------------------------------------------------

function setVideoStatus(msg, level) {
  const el = document.getElementById('video-status');
  if (!el) return;
  el.textContent = msg || '';
  el.className = 'video-status' + (level ? ' ' + level : '');
}

function showVideoElement() {
  const v = document.getElementById('video-player');
  const e = document.getElementById('video-empty');
  const c = document.getElementById('video-controls');
  if (v) v.style.display = 'block';
  if (e) e.style.display = 'none';
  if (c) c.style.display = 'flex';
}

function hideVideoElement(emptyMsg) {
  const v = document.getElementById('video-player');
  const e = document.getElementById('video-empty');
  const c = document.getElementById('video-controls');
  if (v) {
    try { v.pause(); } catch (_) {}
    v.removeAttribute('src');
    v.load();
    v.style.display = 'none';
  }
  if (e) {
    e.textContent = emptyMsg || 'No video for this lap';
    e.style.display = 'flex';
  }
  // Keep video controls visible so user can switch laps even when current has no video
  videoState.frames = null;
  videoState.framesByNd = null;
  videoState.isPlaying = false;
}

function onVideoSpeedChange(rate) {
  if (!videoState.element) return;
  const r = parseFloat(rate);
  if (Number.isFinite(r) && r > 0) videoState.element.playbackRate = r;
}

function _currentVideoSpeed() {
  const el = document.getElementById('video-speed');
  const r = el ? parseFloat(el.value) : 1;
  return Number.isFinite(r) && r > 0 ? r : 1;
}

function populateVideoLapPicker(selections) {
  const select = document.getElementById('video-lap-select');
  if (!select) return;

  videoState.laps = (selections || []).slice();
  select.innerHTML = '';

  if (!videoState.laps.length) {
    select.appendChild(new Option('— pick laps and Plot —', ''));
    select.disabled = true;
    hideVideoElement('Pick laps and click Plot');
    setVideoStatus('');
    return;
  }

  videoState.laps.forEach((sel, i) => {
    const sid = sel.key.session_id || '?';
    // Short session display: just the timestamp portion
    const shortSid = sid.length > 19 ? sid.slice(11, 19) : sid;
    const text = `${sel.label} • ${shortSid}`;
    select.appendChild(new Option(text, String(i)));
  });
  select.disabled = false;
  const vc = document.getElementById('video-controls');
  if (vc) vc.style.display = 'flex';

  // Auto-load the first lap if nothing was loaded yet (preserve user's choice
  // across re-plots if the same lap is still in the list).
  let preserveIdx = -1;
  if (videoState.currentLapIdx >= 0) {
    const prev = videoState.laps[videoState.currentLapIdx];
    if (prev) preserveIdx = videoState.currentLapIdx;
  }
  const targetIdx = preserveIdx >= 0 ? preserveIdx : 0;
  select.value = String(targetIdx);
  loadVideoForLapIdx(targetIdx);
}

function onVideoLapChange(idxStr) {
  const idx = parseInt(idxStr, 10);
  if (Number.isFinite(idx)) loadVideoForLapIdx(idx);
}

async function loadVideoForLapIdx(idx) {
  const sel = videoState.laps[idx];
  if (!sel) return;
  videoState.currentLapIdx = idx;
  highlightVideoLapTrace(sel.label);

  const sid = sel.key.session_id;
  const lap = sel.lap;
  if (!sid && sid !== 0) {
    hideVideoElement('Selection has no session_id');
    setVideoStatus('');
    return;
  }

  const token = ++videoState.currentLoadToken;
  setVideoStatus('Loading video...');

  let meta;
  try {
    const url = `/api/video/${encodeURIComponent(sid)}/${lap}`;
    const res = await fetch(url);
    if (!res.ok) {
      hideVideoElement('Video unavailable (HTTP ' + res.status + ')');
      setVideoStatus('HTTP ' + res.status, 'error');
      return;
    }
    meta = await res.json();
  } catch (e) {
    hideVideoElement('Video request failed');
    setVideoStatus(e.message || String(e), 'error');
    return;
  }

  // A newer load may have started while we awaited — drop stale results
  if (token !== videoState.currentLoadToken) return;

  if (!meta || !meta.has_video) {
    hideVideoElement(meta && meta.message || 'No video for this lap');
    setVideoStatus('');
    return;
  }

  showVideoElement();
  const video = videoState.element;

  // Build sync lookups first (doesn't depend on video download)
  if (meta.has_sync && meta.sync && Array.isArray(meta.sync.frames)) {
    buildSyncLookups(meta.sync);
  } else {
    videoState.frames = null;
    videoState.framesByNd = null;
  }

  if (!video) return;
  try { video.pause(); } catch (_) {}
  videoState.isPlaying = false;

  // Release previous blob URL
  if (videoState.blobUrl) {
    URL.revokeObjectURL(videoState.blobUrl);
    videoState.blobUrl = null;
  }

  // Fetch the full MP4 into memory (up to 100 MB) so seeking is instant.
  // Larger files fall back to streaming.
  const MAX_BLOB_BYTES = 100 * 1048576;
  setVideoStatus('Buffering video...');
  try {
    const headResp = await fetch(meta.mp4_url, { method: 'HEAD' });
    if (token !== videoState.currentLoadToken) return;
    const contentLen = parseInt(headResp.headers.get('Content-Length') || '0', 10);

    let sizeMB = '?';
    if (contentLen > 0 && contentLen <= MAX_BLOB_BYTES) {
      const resp = await fetch(meta.mp4_url);
      if (token !== videoState.currentLoadToken) return;
      if (!resp.ok) {
        setVideoStatus('Failed to buffer video (HTTP ' + resp.status + ')', 'error');
        return;
      }
      const blob = await resp.blob();
      if (token !== videoState.currentLoadToken) return;
      videoState.blobUrl = URL.createObjectURL(blob);
      video.src = videoState.blobUrl;
      sizeMB = (blob.size / 1048576).toFixed(1);
    } else {
      // Too large or unknown size — stream directly
      video.src = meta.mp4_url;
      if (contentLen > 0) sizeMB = (contentLen / 1048576).toFixed(1);
    }
    video.currentTime = 0;
    video.playbackRate = _currentVideoSpeed();
    // Compute timebase correction once MP4 metadata loads
    video.addEventListener('loadedmetadata', function _onMeta() {
      video.removeEventListener('loadedmetadata', _onMeta);
      const mp4Dur = video.duration * 1000;
      if (videoState.sidecarDurationMs > 0 && mp4Dur > 0) {
        videoState.timeScale = videoState.sidecarDurationMs / mp4Dur;
      }
    }, { once: true });
    video.play().catch(() => {});

    if (videoState.frames) {
      const dur = meta.sync.duration_ms ? (meta.sync.duration_ms / 1000).toFixed(1) + 's' : '?';
      setVideoStatus(`${sizeMB} MB • ${dur} • sync ${videoState.frames.length} pts`);
    } else {
      setVideoStatus(
        `${sizeMB} MB` + (meta.message ? ' • ' + meta.message : ''),
        meta.has_sync ? '' : 'warn'
      );
    }
  } catch (e) {
    if (token !== videoState.currentLoadToken) return;
    setVideoStatus('Video buffer failed: ' + (e.message || e), 'error');
  }
}

function buildSyncLookups(sync) {
  const valid = (sync.frames || []).filter(
    f => f && Number.isFinite(f.t_ms) && f.normPos != null && Number.isFinite(f.normPos)
  );
  if (!valid.length) {
    videoState.frames = null;
    videoState.framesByNd = null;
    return;
  }
  // Two views: by t_ms for video-playback → marker, by normPos for
  // marker-drag → video. Maintaining both is critical because normPos is
  // NOT monotonic on out-laps (wraps from ~1.0 → 0.0 across the S/F line),
  // and binary-searching a t_ms-sorted array by normPos snaps drags to
  // either the first or last frame.
  videoState.frames = valid.slice().sort((a, b) => a.t_ms - b.t_ms);
  videoState.framesByNd = valid.slice().sort((a, b) => a.normPos - b.normPos);
  // Sidecar duration for timebase correction (MP4 duration may differ)
  videoState.sidecarDurationMs = valid.length ? valid[valid.length - 1].t_ms : 0;
  videoState.timeScale = 1; // updated once video metadata loads
}

function _interp(arr, keyFn, valFn, target) {
  if (!arr || !arr.length) return null;
  if (target <= keyFn(arr[0])) return valFn(arr[0]);
  if (target >= keyFn(arr[arr.length - 1])) return valFn(arr[arr.length - 1]);
  let lo = 0, hi = arr.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (keyFn(arr[mid]) <= target) lo = mid; else hi = mid;
  }
  const k0 = keyFn(arr[lo]), k1 = keyFn(arr[hi]);
  if (k1 === k0) return valFn(arr[lo]);
  const frac = (target - k0) / (k1 - k0);
  return valFn(arr[lo]) + frac * (valFn(arr[hi]) - valFn(arr[lo]));
}

function lookupTmsForNormPos(nd) {
  return _interp(videoState.framesByNd, f => f.normPos, f => f.t_ms, nd);
}

function lookupNormPosForTms(t_ms) {
  return _interp(videoState.frames, f => f.t_ms, f => f.normPos, t_ms);
}

function syncVideoFromMarker(nd, source) {
  // Called from updateMarker.
  //   source==='video' : called by the video timeupdate handler — don't echo.
  //   source==='drag'  : user dragged the marker → pause video (if playing),
  //                      then seek to the matching frame.
  //   else             : programmatic re-render (plot/init/etc) → leave video alone.
  const v = videoState.element;
  if (!v || !videoState.frames) return;
  if (source !== 'drag') return;
  if (videoState.isPlaying) {
    try { v.pause(); } catch (_) {}
  }
  const t_ms = lookupTmsForNormPos(nd);
  if (t_ms == null) return;
  const scale = videoState.timeScale || 1;
  const target = (t_ms / scale) / 1000;
  // Smaller-than-frame deltas would just churn the video element. At 30 fps
  // a frame is ~33ms; 15ms is ~half a frame and still feels responsive while
  // dragging.
  if (Math.abs(v.currentTime - target) > 0.015) {
    v.currentTime = target;
  }
}

// ---------- requestVideoFrameCallback path (frame-accurate) ----------------
function _onVideoFrame(_now, metadata) {
  if (!videoState.isPlaying || !videoState.frames || !videoState.element) return;
  const scale = videoState.timeScale || 1;
  const nd = lookupNormPosForTms(metadata.mediaTime * 1000 * scale);
  if (nd != null) {
    updateMarker(Math.max(0, Math.min(1, nd)), true, 'video');
  }
  // Re-register for the next displayed frame while still playing.
  if (videoState.isPlaying) {
    videoState.element.requestVideoFrameCallback(_onVideoFrame);
  }
}

// ---------- rAF fallback path (old browsers without RVFC) ------------------
function _videoRafLoop() {
  if (!videoState.isPlaying || !videoState.frames || !videoState.element) {
    videoState._rafId = null;
    return;
  }
  const now = performance.now();
  if (now - videoState._lastRafUpdate >= VIDEO_RAF_INTERVAL_MS) {
    const rafScale = videoState.timeScale || 1;
    const nd = lookupNormPosForTms(videoState.element.currentTime * 1000 * rafScale);
    if (nd != null) {
      updateMarker(Math.max(0, Math.min(1, nd)), true, 'video');
    }
    videoState._lastRafUpdate = now;
  }
  videoState._rafId = requestAnimationFrame(_videoRafLoop);
}

function _startVideoSync(v) {
  if (HAS_RVFC) {
    v.requestVideoFrameCallback(_onVideoFrame);
  } else {
    if (videoState._rafId == null) {
      videoState._lastRafUpdate = 0;
      videoState._rafId = requestAnimationFrame(_videoRafLoop);
    }
  }
}

function _stopVideoSync() {
  if (!HAS_RVFC && videoState._rafId != null) {
    cancelAnimationFrame(videoState._rafId);
    videoState._rafId = null;
  }
  // RVFC path stops automatically: _onVideoFrame checks isPlaying before
  // re-registering, so no explicit cancel is needed.
}

function _wireVideoElement() {
  const v = document.getElementById('video-player');
  if (!v) return;
  videoState.element = v;

  v.addEventListener('play', () => {
    videoState.isPlaying = true;
    _startVideoSync(v);
  });
  v.addEventListener('pause', () => {
    videoState.isPlaying = false;
    _stopVideoSync();
  });
  v.addEventListener('ended', () => {
    videoState.isPlaying = false;
    _stopVideoSync();
  });

  // While paused, native HTML5 video controls (or the user's marker drag)
  // can step the frame; reflect any seek in the marker so the dot follows.
  v.addEventListener('seeked', () => {
    if (videoState.isPlaying || !videoState.frames) return;
    const seekScale = videoState.timeScale || 1;
    const nd = lookupNormPosForTms(v.currentTime * 1000 * seekScale);
    if (nd == null) return;
    updateMarker(Math.max(0, Math.min(1, nd)), true, 'video');
  });
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
(async () => {
  setStatus('<span class="loading-spinner"></span> Loading...');
  _wireVideoElement();

  // Render the first row immediately so the user sees dropdown placeholders
  // in a "loading…" state rather than a blank panel while we fetch sessions.
  const firstRow = document.createElement('div');
  firstRow.className = 'selection-row';
  firstRow.id = 'row-loading';
  firstRow.innerHTML = PART_COLS.map(col => `
    <div class="part-group">
      <span class="part-label">${PART_LABELS[col]}</span>
      <select class="part-select" disabled>
        <option>loading…</option>
      </select>
    </div>`).join('') + `
    <div class="part-group">
      <span class="part-label">Laps</span>
      <div class="lap-checkboxes"><span class="lap-info">...</span></div>
    </div>`;
  document.getElementById('selections').appendChild(firstRow);

  try {
    const urlParams = new URLSearchParams(window.location.search);
    const defaults = {};
    for (const col of PART_COLS) {
      const val = urlParams.get(col);
      if (val) defaults[col] = val;
    }
    const isDeepLink = Object.keys(defaults).length > 0;

    if (isDeepLink) {
      // Fast path: fetch ONLY the matching session (~300-400 ms), render
      // immediately. Then refetch the full list in the background so that
      // cascading dropdowns work when the user wants to compare.
      const [filtered] = await Promise.all([
        fetchSessions(defaults),
        loadChannels(),
        fetchTrack(),
      ]);

      if (filtered.length === 0) {
        // The URL params didn't match any session. Skip the broken render
        // and fall back to direct-access: load the full list and let the
        // user pick from what actually exists. Warn them with a toast.
        const summary = Object.entries(defaults)
          .map(([k, v]) => `${k}=${v}`)
          .join(', ');
        showToast(
          {
            title: 'No sessions match the URL parameters',
            detail: `${summary}\nShowing the full list instead.`,
          },
          'warn',
        );
        const full = await fetchSessions();
        sessions = full;
        firstRow.remove();
        addRow(null);
        setStatus(`${sessions.length} sessions loaded. Select partitions, check laps, then click Plot.`);
      } else {
        sessions = filtered;
        firstRow.remove();
        addRow(defaults);
        setStatus('<span class="loading-spinner"></span> Loading full session list in background...');

        fetchSessions().then(full => {
          sessions = full;
          // Refresh dropdown option lists in every row while preserving the
          // user's current selections.
          document.querySelectorAll('.selection-row').forEach(row => {
            const idx = parseInt(row.dataset.rowIdx);
            if (!Number.isFinite(idx)) return;
            const current = getRowFilters(idx);
            populateDropdowns(idx, 0, current);
          });
          setStatus(`${sessions.length} sessions loaded. Select partitions, check laps, then click Plot.`);
        }).catch(e => {
          setStatus('Background load failed: ' + e.message, true);
          showToast(
            {
              title: 'Loaded the requested session, but the full list failed to load',
              detail: e.message,
            },
            'warn',
          );
        });
      }
    } else {
      // Direct access: full fetch up-front, then render row.
      const [full] = await Promise.all([
        fetchSessions(),
        loadChannels(),
        fetchTrack(),
      ]);
      sessions = full;
      firstRow.remove();
      addRow(null);
      setStatus(`${sessions.length} sessions loaded. Select partitions, check laps, then click Plot.`);
    }
  } catch (e) {
    firstRow.remove();
    setStatus('Failed to initialize: ' + e.message, true);
    // Leave a visible recovery hint in the selections panel — otherwise a
    // user who dismisses the toast sees a blank panel with no guidance.
    const fallback = document.createElement('div');
    fallback.className = 'part-label';
    fallback.style.padding = '1rem';
    fallback.textContent = 'Failed to load sessions. Fix the issue and reload the page.';
    document.getElementById('selections').appendChild(fallback);
    showToast(
      { title: 'Failed to load telemetry data', detail: e.message },
      'error',
    );
  }
})();
