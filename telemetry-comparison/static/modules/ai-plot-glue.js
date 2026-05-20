/**
 * Apply an AI Mode-1 plot plan to the existing manual UI: pre-fill rows
 * from the plan's `traces[]`, tick the requested laps, activate signal
 * chips, then click Plot. Lets the AI drive the same surfaces the user
 * already touches by hand.
 *
 * Plan shape (matches QuixLake Querier agent output):
 *   {
 *     type: "plot",
 *     title: string,
 *     signals: string[],
 *     traces: [{session_id, lap, driver, carModel, track,
 *               experiment, environment, test_rig}, ...]
 *   }
 *
 * Trace = one (session_id, lap) row. Multiple traces with the same
 * partition tuple but different laps collapse into one row with multiple
 * lap checkboxes ticked.
 */

import { appState, PART_COLS } from './state.js';
import { addRow } from './selections.js';

/**
 * Group plan.traces[] by their non-lap partition key.
 * Returns Map<partitionTupleJSON, {partition, laps[]}>.
 */
function _groupTraces(traces) {
  const groups = new Map();
  for (const trace of traces) {
    const partition = {};
    for (const col of PART_COLS) partition[col] = trace[col];
    const key = JSON.stringify(partition);
    if (!groups.has(key)) groups.set(key, { partition, laps: [] });
    groups.get(key).laps.push(trace.lap);
  }
  return groups;
}

function _clearExistingRows() {
  const container = document.getElementById('selections');
  if (container) container.innerHTML = '';
  appState.rowCount = 0;
}

function _tickLaps(rowIdx, laps) {
  for (const lap of laps) {
    const input = document.querySelector(`#laps-${rowIdx} input[value="${lap}"]`);
    if (!input) continue;
    input.checked = true;
    const label = input.closest('.lap-cb');
    if (label) label.classList.add('checked');
  }
}

function _activateSignals(signals) {
  const wanted = new Set(signals);
  document.querySelectorAll('#signal-container .chip').forEach((chip) => {
    chip.classList.toggle('active', wanted.has(chip.dataset.signal));
  });
}

/**
 * Apply a Mode-1 plot plan and trigger Plot. Returns true on success.
 */
export function applyPlotPlan(plan) {
  if (!plan || plan.type !== 'plot' || !Array.isArray(plan.traces)) {
    return false;
  }

  _clearExistingRows();

  const groups = _groupTraces(plan.traces);
  for (const { partition, laps } of groups.values()) {
    addRow(partition);
    // addRow incremented appState.rowCount; the row we just added has
    // index = rowCount - 1 (addRow assigns idx = rowCount++, then bumps).
    const rowIdx = appState.rowCount - 1;
    _tickLaps(rowIdx, laps);
  }

  _activateSignals(plan.signals || []);

  // window.plot exposed by charts.js for inline-HTML onclick="plot()".
  if (typeof window.plot === 'function') {
    window.plot();
    return true;
  }
  return false;
}
