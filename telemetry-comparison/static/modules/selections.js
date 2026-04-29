/**
 * Row + dropdown + lap-picker UI and channel-chip rendering.
 *
 * Cascading dropdown logic: each row has a fixed column order
 * (PART_COLS). Changing one column re-populates every downstream column
 * with distinct values filtered by upstream selections. When a column
 * has only one possible value it auto-selects to match the Test Manager
 * pattern (no placeholder noise).
 *
 * Inline HTML handlers re-exposed on `window` at the bottom:
 *   onPartChange, removeRow, toggleAllLaps, addRow, toggleCat
 */

import {
  appState,
  PART_COLS,
  PART_LABELS,
  ROW_COLORS,
  TRACE_COLORS,
  DEFAULT_ACTIVE,
  CAT_ORDER,
  MAX_VISIBLE,
} from './state.js';
import { getDistinctValues } from './data.js';

export function getRowFilters(idx) {
  const filters = {};
  for (const col of PART_COLS) {
    const sel = document.getElementById(`${col}-${idx}`);
    if (sel) filters[col] = sel.value;
  }
  return filters;
}

export function getPreviousRowFilters() {
  const rows = document.querySelectorAll('.selection-row');
  if (!rows.length) return {};
  const last = rows[rows.length - 1];
  return getRowFilters(parseInt(last.dataset.rowIdx));
}

export function addRow(defaults) {
  const idx = appState.rowCount++;
  // Row accent colour — rotates independently of trace colouring so a row's
  // visual identity survives across re-plots.
  const color = ROW_COLORS[idx % ROW_COLORS.length];
  void color; // assigned for parity with original; currently unused in template
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
    <button class="btn btn-xs btn-danger" onclick="removeRow(${idx})" aria-label="Remove session">
      <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M4 4 L12 12 M12 4 L4 12"/>
      </svg>
    </button>`;

  row.innerHTML = html;
  document.getElementById('selections').appendChild(row);
  populateDropdowns(idx, 0, prev);
}

export function removeRow(idx) {
  const el = document.getElementById('row-' + idx);
  if (el) el.remove();
}

export function populateDropdowns(rowIdx, fromColIdx, defaults) {
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
      const display =
        col === 'session_id' ? String(v).substring(0, 19).replace('T', ' ') : v || '(empty)';
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

export function onPartChange(rowIdx, changedCol) {
  const colIdx = PART_COLS.indexOf(changedCol);
  populateDropdowns(rowIdx, colIdx + 1, null);
}

export function loadLaps(rowIdx) {
  const container = document.getElementById(`laps-${rowIdx}`);
  const filters = getRowFilters(rowIdx);

  if (!PART_COLS.every((c) => filters[c])) {
    container.innerHTML = '<span class="lap-info">...</span>';
    return;
  }

  // Laps are baked into each session object by /api/sessions — no extra
  // HTTP call. Find the matching pre-loaded session and render.
  const session = appState.sessions.find((s) =>
    PART_COLS.every((c) => String(s[c]) === String(filters[c])),
  );
  const laps = session?.laps || [];

  if (!laps.length) {
    container.innerHTML = '<span class="lap-info">no laps</span>';
    return;
  }

  container.innerHTML =
    `
    <button class="btn btn-xs btn-outline" onclick="toggleAllLaps(${rowIdx})">all</button>
  ` +
    laps
      .map(
        (lap) => `
    <label class="lap-cb" onclick="event.preventDefault(); this.classList.toggle('checked'); this.querySelector('input').checked = this.classList.contains('checked');">
      <input type="checkbox" value="${lap}">
      L${lap}
    </label>
  `,
      )
      .join('');
}

export function toggleAllLaps(idx) {
  const labels = document.querySelectorAll(`#laps-${idx} .lap-cb`);
  const anyUnchecked = Array.from(labels).some((l) => !l.classList.contains('checked'));
  labels.forEach((l) => {
    l.classList.toggle('checked', anyUnchecked);
    l.querySelector('input').checked = anyUnchecked;
  });
}

// ---------------------------------------------------------------------------
// Gather selections for plotting
// ---------------------------------------------------------------------------

export function getSelections() {
  const result = [];
  let colorIdx = 0;

  // Collect all row session_ids first to detect multi-session
  const rows = document.querySelectorAll('.selection-row');
  const sessionIds = [];
  rows.forEach((row) => {
    const filters = getRowFilters(parseInt(row.dataset.rowIdx));
    sessionIds.push(filters.session_id || '');
  });
  const uniqueSessions = [...new Set(sessionIds)];
  const multiSession = uniqueSessions.length > 1;

  rows.forEach((row, rowIdx) => {
    const filters = getRowFilters(parseInt(row.dataset.rowIdx));
    const sIdx = multiSession ? uniqueSessions.indexOf(sessionIds[rowIdx]) + 1 : -1;

    const checked = row.querySelectorAll('.lap-cb input:checked');
    checked.forEach((cb) => {
      const lap = parseInt(cb.value);
      const label = multiSession ? `S${sIdx}-L${lap}` : `L${lap}`;
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
// Channels / Signals — chip rendering, category toggle, active-signal query
// ---------------------------------------------------------------------------

export function getActiveSignals() {
  return Array.from(document.querySelectorAll('.chip.active')).map((c) => c.dataset.signal);
}

export function chartTitle(col) {
  const ch = appState.channels[col];
  return ch ? `${ch.label} ${ch.unit}` : col;
}

export function renderChannelChips() {
  const container = document.getElementById('signal-container');
  const groups = {};
  for (const [col, meta] of Object.entries(appState.channels)) {
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
    if (collapsed)
      html += `<button class="cat-toggle" onclick="toggleCat('${catId}', this)">show all ${cols.length}</button>`;
    html += `</div><div class="signal-chips" id="cat-${catId}">`;
    for (let i = 0; i < cols.length; i++) {
      const col = cols[i];
      const m = appState.channels[col];
      const active = DEFAULT_ACTIVE.has(col) ? ' active' : '';
      const hidden = collapsed && i >= MAX_VISIBLE ? ' style="display:none" data-extra' : '';
      html += `<span class="chip${active}" data-signal="${col}"${hidden}>${m.label}</span>`;
    }
    html += '</div></div>';
  }
  container.innerHTML = html;
  container.addEventListener('click', (e) => {
    if (e.target.classList.contains('chip')) e.target.classList.toggle('active');
  });
}

export function toggleCat(catId, btn) {
  const chips = document.querySelectorAll(`#cat-${catId} [data-extra]`);
  const showing = chips[0]?.style.display !== 'none';
  chips.forEach((c) => (c.style.display = showing ? 'none' : ''));
  btn.textContent = showing ? 'show all' : 'show less';
}

// ---------------------------------------------------------------------------
// Expose inline-HTML handlers on window.
// The handlers are invoked by string templates built in this module
// (addRow, loadLaps, renderChannelChips) as well as by static markup in
// index.html (addRow on the + Add Session button).
// ---------------------------------------------------------------------------

window.addRow = addRow;
window.removeRow = removeRow;
window.onPartChange = onPartChange;
window.toggleAllLaps = toggleAllLaps;
window.toggleCat = toggleCat;
