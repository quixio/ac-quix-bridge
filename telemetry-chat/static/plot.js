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
 * Fetch channels.json once and cache the promise so every subsequent render
 * reuses the result. Used by `chartTitle` for human-readable axis labels.
 * @type {Promise<Record<string, {label: string, unit: string}>> | null}
 */
let channelsPromise = null;

/**
 * @returns {Promise<Record<string, {label: string, unit: string}>>}
 */
function loadChannels() {
  if (!channelsPromise) {
    channelsPromise = fetch("/static/channels.json").then((r) => r.json());
  }
  return channelsPromise;
}

/**
 * Format a column name as "Label [unit]" for axis titles. Falls back to
 * the raw column when the channel isn't in the metadata.
 * @param {string} col
 * @param {Record<string, {label: string, unit: string}>} channels
 * @returns {string}
 */
function chartTitle(col, channels) {
  const ch = channels[col];
  return ch ? `${ch.label} ${ch.unit}` : col;
}

/**
 * Render one Plotly chart per signal, stacked inside the container.
 * The container is expected to have overflow-y:auto so > 2-3 charts can scroll.
 * @param {HTMLElement} container
 * @param {Chart[]} charts
 * @returns {Promise<void>}
 */
export async function renderCharts(container, charts) {
  const channels = await loadChannels();
  // Purge existing Plotly instances before wiping the DOM — innerHTML=""
  // alone leaks WebGL contexts + resize observers that Plotly attaches.
  clearCharts(container);
  for (const chart of charts) {
    const div = document.createElement("div");
    div.className = "chart";
    container.appendChild(div);
    // @ts-ignore – Plotly is loaded via CDN script tag.
    Plotly.newPlot(div, buildTraces(chart.traces), layoutFor(chart.signal, channels), {
      displayModeBar: true,
      responsive: true,
    });
  }
}

/**
 * @param {string} signal
 * @param {Record<string, {label: string, unit: string}>} channels
 * @returns {Object}
 */
function layoutFor(signal, channels) {
  return {
    paper_bgcolor: "#1a1d27",
    plot_bgcolor: "#1a1d27",
    font: { color: "#e2e8f0", family: "ui-sans-serif, system-ui", size: 11 },
    margin: { l: 55, r: 60, t: 10, b: 55 },
    xaxis: {
      title: chartTitle("normalizedCarPosition", channels),
      color: "#8892a4",
      gridcolor: "#2d3047",
      zerolinecolor: "#2d3047",
    },
    yaxis: {
      title: chartTitle(signal, channels),
      color: "#8892a4",
      gridcolor: "#2d3047",
      zerolinecolor: "#2d3047",
    },
    legend: { orientation: "v", x: 1.02, y: 1, font: { size: 10 } },
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
