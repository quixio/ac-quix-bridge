/**
 * Telemetry Explorer bootstrap.
 *
 * Loaded as an ES module — see index.html <script type="module">.
 * Wires cross-module dependencies, exposes a few panel-toggle handlers that
 * don't belong to a single feature module, and runs the init IIFE that
 * handles the deep-link fast path vs. direct-access full load.
 */

import { appState, PART_COLS, PART_LABELS } from './modules/state.js';
import { fetchSessions, fetchTrack, fetchLayouts, fetchChannels } from './modules/data.js';
import { wireVideoElement } from './modules/sync.js';
import {
  addRow,
  renderChannelChips,
  getRowFilters,
  populateDropdowns,
} from './modules/selections.js';
import { setStatus } from './modules/charts.js';
// Side-effect import: installs window.onVideoLapChange/onVideoSpeedChange
// and the document 'plot-complete' listener that drives the lap picker.
import './modules/video.js';
import { initChatOverlay } from './modules/chat-overlay.js';
import { initChat } from './modules/chat.js';
import { initAuth, installFetchOverride } from './modules/auth.js';

// Replace window.fetch BEFORE any module-level call fires so /api/* requests
// from data.js, chat.js, video.js, etc. all transparently carry the Bearer.
installFetchOverride();

// ---------------------------------------------------------------------------
// Panel collapse/expand — keyed to inline onclick= in index.html. The CSS
// hides `[data-collapsible-body]` on any ancestor with data-collapsed=true.
// ---------------------------------------------------------------------------

function togglePanel(panelId, btn) {
  const p = document.getElementById(panelId);
  if (!p) return;
  const collapsed = p.dataset.collapsed === 'true';
  p.dataset.collapsed = collapsed ? 'false' : 'true';
  if (btn) btn.textContent = collapsed ? '-' : '+';
}

function toggleTopbarPanel(panelId, btn) {
  togglePanel(panelId, btn);
}

window.togglePanel = togglePanel;
window.toggleTopbarPanel = toggleTopbarPanel;

// ---------------------------------------------------------------------------
// Active-track → layout → geometry wiring.
//
// The track map reflects ONE track: the FIRST selection row's `track` value
// (documented single-map assumption). When that track changes we re-resolve
// its Mongo layouts and reload geometry. The LAYOUT <select> is app-level
// (#layout-select in the Track Map header), not per-row.
// ---------------------------------------------------------------------------

let _activeTrack = '';

/** The track that drives the map: the first selection row's `track` value. */
function getActiveTrack() {
  const row = document.querySelector('.selection-row');
  if (!row) return '';
  const idx = parseInt(row.dataset.rowIdx);
  if (!Number.isFinite(idx)) return '';
  const sel = document.getElementById(`track-${idx}`);
  return sel?.value || '';
}

/**
 * Resolve layouts for `track`, populate/show/hide the LAYOUT dropdown, and
 * fetch geometry. 0 layouts → hide + CSV-fallback geometry; 1 → hide +
 * auto-select; >1 → show, auto-select first. Empty track → hide + param-less
 * fetchTrack (CSV fallback, preserves first paint).
 */
async function refreshTrackForActive(track) {
  _activeTrack = track || '';
  const dd = document.getElementById('layout-select');

  if (!track) {
    if (dd) {
      dd.style.display = 'none';
      dd.classList.add('hidden');
      dd.innerHTML = '';
    }
    fetchTrack();
    return;
  }

  const layouts = await fetchLayouts(track);

  // Guard: a newer active-track change may have superseded this one.
  if (track !== _activeTrack) return;

  if (!dd) {
    fetchTrack(track);
    return;
  }

  if (layouts.length > 1) {
    dd.innerHTML = layouts
      .map((l) => `<option value="${l.layout}">${l.layout} (${l.n_corners ?? '?'} cnr)</option>`)
      .join('');
    dd.value = layouts[0].layout;
    dd.style.display = '';
    dd.classList.remove('hidden');
    fetchTrack(track, layouts[0].layout);
  } else {
    // 0 or 1 layout: hide the dropdown. 1 → auto-select that layout; 0 → no
    // layout param (server resolves the single doc or CSV-falls back).
    dd.style.display = 'none';
    dd.classList.add('hidden');
    dd.innerHTML = '';
    fetchTrack(track, layouts.length === 1 ? layouts[0].layout : '');
  }
}

/** LAYOUT dropdown onchange handler (inline in index.html). */
function onLayoutChange(layout) {
  if (_activeTrack) fetchTrack(_activeTrack, layout);
}
window.onLayoutChange = onLayoutChange;

// Wrap selections.js's window.onPartChange so the map re-resolves layouts +
// geometry whenever the active (first) row's `track` value changes — even when
// it changes implicitly. The original handler runs first: changing ANY column
// cascades downstream (populateDropdowns), which programmatically re-selects
// the `track` <select>. A programmatic `sel.value = ...` does NOT fire its
// onchange, so keying off `changedCol === 'track'` alone misses the common case
// (e.g. picking an experiment/driver that narrows to Monza auto-selects
// track=monza without an explicit track-dropdown change). We therefore compare
// the active track before vs. after the cascade and refresh on any difference.
const _origOnPartChange = window.onPartChange;
window.onPartChange = function (rowIdx, changedCol) {
  if (typeof _origOnPartChange === 'function') _origOnPartChange(rowIdx, changedCol);
  // The map follows the FIRST selection row only (single-map contract). A
  // change in any other row can't alter the active track, so skip the work.
  const firstRow = document.querySelector('.selection-row');
  if (!firstRow || parseInt(firstRow.dataset.rowIdx) !== rowIdx) return;
  const newTrack = getActiveTrack();
  if (newTrack !== _activeTrack) refreshTrackForActive(newTrack);
};

// ---------------------------------------------------------------------------
// Init — runs once the module is evaluated (type="module" defers until DOM
// is parsed, so document.getElementById is safe).
// ---------------------------------------------------------------------------

(async () => {
  setStatus('<span class="loading-spinner"></span> Loading...');
  // Block until we have a valid Bearer token — every /api/* call beyond
  // this point relies on it. initAuth runs the embedded handshake (postMessage
  // to parent) or the PAT dialog (standalone).
  try {
    await initAuth();
  } catch (e) {
    setStatus('Authentication failed: ' + e.message, true);
    return;
  }
  wireVideoElement();
  initChatOverlay();
  initChat();

  // Render the first row immediately so the user sees dropdown placeholders
  // in a "loading…" state rather than a blank panel while we fetch sessions.
  const firstRow = document.createElement('div');
  firstRow.className = 'selection-row';
  firstRow.id = 'row-loading';
  firstRow.innerHTML =
    PART_COLS.map(
      (col) => `
    <div class="part-group">
      <span class="part-label">${PART_LABELS[col]}</span>
      <select class="part-select" disabled>
        <option>loading…</option>
      </select>
    </div>`,
    ).join('') +
    `
    <div class="part-group">
      <span class="part-label">Laps</span>
      <div class="lap-checkboxes"><span class="lap-info">...</span></div>
    </div>`;
  document.getElementById('selections').appendChild(firstRow);

  // loadChannels = fetch + render chips (same two-step as before).
  const loadChannels = async () => {
    await fetchChannels();
    renderChannelChips();
  };

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
      const [filtered] = await Promise.all([fetchSessions(defaults), loadChannels(), fetchTrack()]);

      if (filtered.length === 0) {
        // The URL params didn't match any session. Skip the broken render
        // and fall back to direct-access: load the full list and let the
        // user pick from what actually exists. Warn them with a toast.
        const summary = Object.entries(defaults)
          .map(([k, v]) => `${k}=${v}`)
          .join(', ');
        window.showToast(
          {
            title: 'No sessions match the URL parameters',
            detail: `${summary}\nShowing the full list instead.`,
          },
          'warn',
        );
        const full = await fetchSessions();
        appState.sessions = full;
        firstRow.remove();
        addRow(null);
        refreshTrackForActive(getActiveTrack());
        setStatus(
          `${appState.sessions.length} sessions loaded. Select partitions, check laps, then click Plot.`,
        );
      } else {
        appState.sessions = filtered;
        firstRow.remove();
        addRow(defaults);
        // Row now has a resolved track; load its Mongo layouts + geometry
        // (replaces the param-less first-paint fetchTrack above).
        refreshTrackForActive(getActiveTrack());
        setStatus(
          '<span class="loading-spinner"></span> Loading full session list in background...',
        );

        fetchSessions()
          .then((full) => {
            appState.sessions = full;
            // Refresh dropdown option lists in every row while preserving the
            // user's current selections.
            document.querySelectorAll('.selection-row').forEach((row) => {
              const idx = parseInt(row.dataset.rowIdx);
              if (!Number.isFinite(idx)) return;
              const current = getRowFilters(idx);
              populateDropdowns(idx, 0, current);
            });
            setStatus(
              `${appState.sessions.length} sessions loaded. Select partitions, check laps, then click Plot.`,
            );
          })
          .catch((e) => {
            setStatus('Background load failed: ' + e.message, true);
            window.showToast(
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
      const [full] = await Promise.all([fetchSessions(), loadChannels(), fetchTrack()]);
      appState.sessions = full;
      firstRow.remove();
      addRow(null);
      refreshTrackForActive(getActiveTrack());
      setStatus(
        `${appState.sessions.length} sessions loaded. Select partitions, check laps, then click Plot.`,
      );
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
    window.showToast({ title: 'Failed to load telemetry data', detail: e.message }, 'error');
  }
})();
