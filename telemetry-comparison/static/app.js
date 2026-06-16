/**
 * Telemetry Explorer bootstrap.
 *
 * Loaded as an ES module — see index.html <script type="module">.
 * Wires cross-module dependencies, exposes a few panel-toggle handlers that
 * don't belong to a single feature module, and runs the init IIFE that
 * handles the deep-link fast path vs. direct-access full load.
 */

import { appState, PART_COLS, PART_LABELS } from './modules/state.js';
import { fetchSessions, fetchTrack, fetchChannels } from './modules/data.js';
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
        setStatus(
          `${appState.sessions.length} sessions loaded. Select partitions, check laps, then click Plot.`,
        );
      } else {
        appState.sessions = filtered;
        firstRow.remove();
        addRow(defaults);
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
