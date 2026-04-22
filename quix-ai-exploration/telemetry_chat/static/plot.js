/**
 * Plotly trace building + rendering. Ported (and trimmed) from
 * telemetry-comparison's app.js – same colour palette + downsample + overlay.
 */

export const TRACE_COLORS = [
  "#4f8cff",
  "#ff6b6b",
  "#5acf6b",
  "#f4b942",
  "#b57ef2",
  "#2ed4cf",
  "#ff8a65",
  "#8ab4f8",
  "#ffd166",
  "#ef6f6c",
  "#06d6a0",
  "#a29bfe",
  "#fdcb6e",
  "#00cec9",
  "#fd79a8",
  "#81ecec",
];

const MAX_POINTS = 1500;

/**
 * Stride-sample two parallel arrays down to at most `maxPoints` entries.
 * Pure function – identity transform when the input is already ≤ maxPoints.
 * @param {number[]} x
 * @param {number[]} y
 * @param {number} [maxPoints]
 * @returns {{x: number[], y: number[]}}
 */
export function downsample(x, y, maxPoints = MAX_POINTS) {
  if (!x || x.length <= maxPoints) return { x: x ?? [], y: y ?? [] };
  const step = x.length / maxPoints;
  const nx = [];
  const ny = [];
  for (let i = 0; i < maxPoints; i++) {
    const idx = Math.round(i * step);
    nx.push(x[idx]);
    ny.push(y[idx]);
  }
  return { x: nx, y: ny };
}

/**
 * @typedef {Object} ApiTrace
 * @property {string} session_id
 * @property {number} lap
 * @property {string=} driver
 * @property {string=} carModel
 * @property {string=} track
 * @property {string=} experiment
 * @property {number[]} x
 * @property {(number|null)[]} y
 * @property {number} count
 */

/**
 * Turn the backend trace list into Plotly traces.
 * Cross-session labels include an S{n} prefix; same-session get just `L{n}`.
 * @param {ApiTrace[]} apiTraces
 * @returns {Array<Object>}
 */
export function buildTraces(apiTraces) {
  const sessions = [...new Set(apiTraces.map((t) => t.session_id))];
  const sessionIdx = new Map(sessions.map((s, i) => [s, i + 1]));
  const multiSession = sessions.length > 1;

  return apiTraces.map((t, i) => {
    const ds = downsample(t.x, /** @type {number[]} */ (t.y));
    const prefix = multiSession ? `S${sessionIdx.get(t.session_id)}-` : "";
    const driverTag = t.driver ? ` ${t.driver}` : "";
    return {
      x: ds.x,
      y: ds.y,
      type: "scatter",
      mode: "lines",
      name: `${prefix}L${t.lap}${driverTag}`,
      line: { color: TRACE_COLORS[i % TRACE_COLORS.length], width: 1.5 },
    };
  });
}

/**
 * @typedef {Object} Chart
 * @property {string} signal
 * @property {ApiTrace[]} traces
 */

/**
 * Render one Plotly chart per signal, stacked inside the container.
 * The container is expected to have overflow-y:auto so > 2-3 charts can scroll.
 * @param {HTMLElement} container
 * @param {Chart[]} charts
 */
export function renderCharts(container, charts) {
  // Purge existing Plotly instances before wiping the DOM — innerHTML=""
  // alone leaks WebGL contexts + resize observers that Plotly attaches.
  clearCharts(container);
  for (const chart of charts) {
    const div = document.createElement("div");
    div.className = "chart";
    container.appendChild(div);
    // @ts-ignore – Plotly is loaded via CDN script tag.
    Plotly.newPlot(div, buildTraces(chart.traces), layoutFor(chart.signal), {
      displayModeBar: true,
      responsive: true,
    });
  }
}

/**
 * @param {string} signal
 * @returns {Object}
 */
function layoutFor(signal) {
  return {
    paper_bgcolor: "#13161c",
    plot_bgcolor: "#13161c",
    font: { color: "#e6e6e6", family: "ui-sans-serif, system-ui" },
    margin: { l: 60, r: 20, t: 16, b: 40 },
    xaxis: {
      title: "normalizedCarPosition (0 → 1)",
      gridcolor: "#2a2f3a",
      zerolinecolor: "#2a2f3a",
    },
    yaxis: {
      title: signal,
      gridcolor: "#2a2f3a",
      zerolinecolor: "#2a2f3a",
    },
    legend: {
      bgcolor: "rgba(0,0,0,0)",
      bordercolor: "#2a2f3a",
      borderwidth: 1,
      orientation: "v",
    },
    showlegend: true,
  };
}

/**
 * @param {HTMLElement} container
 */
export function clearCharts(container) {
  // Purge all child Plotly instances before clearing to free listeners/buffers.
  for (const child of Array.from(container.children)) {
    // @ts-ignore – Plotly is loaded via CDN.
    Plotly.purge(child);
  }
  container.innerHTML = "";
}
