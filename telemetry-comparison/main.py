"""
Telemetry Comparison — FastAPI service for cross-run/lap telemetry visualization.

Queries Hive-partitioned Parquet data in QuixLake via SQL (DuckDB) and serves
an interactive Plotly.js UI for overlaying telemetry from different sessions/laps.
"""

import os
import logging

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from quixlake import QuixLakeClient

logging.basicConfig(level=os.getenv("LOGLEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(title="Telemetry Comparison")

TABLE_NAME = os.getenv("TABLE_NAME", "ac_telemetry")
QUIXLAKE_URL = os.getenv("QUIXLAKE_URL")
QUIX_LAKE_TOKEN = os.getenv("QUIX_LAKE_TOKEN")


def get_client() -> QuixLakeClient:
    return QuixLakeClient(base_url=QUIXLAKE_URL, token=QUIX_LAKE_TOKEN)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    """List available sessions with their metadata."""
    try:
        client = get_client()
        query = f"""
            SELECT DISTINCT
                session_id,
                track,
                carModel,
                driver,
                experiment,
                MIN(timestamp_ms) as first_ts,
                MAX(timestamp_ms) as last_ts,
                MAX(lap) as max_lap
            FROM {TABLE_NAME}
            GROUP BY session_id, track, carModel, driver, experiment
            ORDER BY first_ts DESC
            LIMIT {limit}
        """
        df = client.query(query)
        df = df.fillna("")
        return JSONResponse(content={"sessions": df.to_dict(orient="records")})
    except Exception as e:
        logger.exception("Failed to list sessions")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/laps")
async def list_laps(session_id: str):
    """List laps for a given session with summary stats."""
    try:
        client = get_client()
        df = client.query(f"""
            SELECT
                lap,
                MIN(normalizedCarPosition) as min_pos,
                MAX(normalizedCarPosition) as max_pos,
                ROUND(AVG(speedKmh), 1) as avg_speed,
                ROUND(MAX(speedKmh), 1) as max_speed,
                COUNT(*) as samples
            FROM {TABLE_NAME}
            WHERE session_id = '{session_id}'
            GROUP BY lap
            ORDER BY lap
        """)
        return JSONResponse(content={"laps": df.to_dict(orient="records")})
    except Exception as e:
        logger.error("Failed to list laps: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/telemetry")
async def get_telemetry(session_id: str, lap: int, signals: str = "speedKmh,gas,brake,steerAngle"):
    """Get telemetry data for a specific session/lap, ordered by track position."""
    signal_list = [s.strip() for s in signals.split(",") if s.strip()]
    # Validate signal names (basic SQL injection prevention)
    for s in signal_list:
        if not s.isidentifier():
            raise HTTPException(status_code=400, detail=f"Invalid signal name: {s}")

    columns = ", ".join(signal_list)
    try:
        client = get_client()
        df = client.query(f"""
            SELECT
                normalizedCarPosition,
                timestamp_ms,
                {columns}
            FROM {TABLE_NAME}
            WHERE session_id = '{session_id}' AND lap = {lap}
            ORDER BY normalizedCarPosition
        """)
        return JSONResponse(content={
            "session_id": session_id,
            "lap": lap,
            "signals": signal_list,
            "count": len(df),
            "data": df.to_dict(orient="list"),
        })
    except Exception as e:
        logger.error("Failed to get telemetry: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals")
async def list_signals():
    """List available signal/column names from the table."""
    try:
        client = get_client()
        df = client.query(f"SELECT * FROM {TABLE_NAME} LIMIT 1")
        # Filter out partition/metadata columns to show telemetry signals
        skip = {"session_id", "lap", "timestamp_ms", "normalizedCarPosition",
                "track", "carModel", "driver", "experiment", "test_id",
                "environment", "test_rig", "beers", "completedLaps",
                "status", "sessionType", "ts_ms"}
        signals = [c for c in df.columns if c not in skip]
        return JSONResponse(content={"signals": sorted(signals)})
    except Exception as e:
        logger.error("Failed to list signals: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Telemetry Comparison</title>
  <script src="https://cdn.plot.ly/plotly-2.35.0.min.js"></script>
  <style>
    :root {
      --bg: #0f1117;
      --surface: #1a1d27;
      --border: #2d3047;
      --text: #e2e8f0;
      --text-muted: #8892a4;
      --accent: #4f8ef7;
      --green: #34d399;
      --orange: #f59e0b;
      --red: #f87171;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace;
      min-height: 100vh;
      padding: 1.5rem;
    }
    h1 { font-size: 1.4rem; font-weight: 600; margin-bottom: 1.5rem; }
    h1 span { color: var(--accent); }

    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem 1.25rem;
      margin-bottom: 1rem;
    }
    .panel-title {
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--text-muted);
      margin-bottom: 0.75rem;
    }

    /* Selection UI */
    .selections { display: flex; flex-direction: column; gap: 0.5rem; }
    .selection-row {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .color-dot {
      width: 12px; height: 12px; border-radius: 50%; flex-shrink: 0;
    }
    select, input[type="text"] {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 0.4rem 0.6rem;
      color: var(--text);
      font-size: 0.85rem;
      outline: none;
    }
    select:focus, input:focus { border-color: var(--accent); }
    select { min-width: 200px; }

    .btn {
      background: var(--accent);
      border: none;
      border-radius: 6px;
      padding: 0.4rem 1rem;
      color: #fff;
      font-size: 0.85rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn:hover { background: #3a7ae4; }
    .btn:disabled { background: var(--border); cursor: wait; }
    .btn-sm { padding: 0.3rem 0.7rem; font-size: 0.78rem; }
    .btn-outline {
      background: transparent;
      border: 1px solid var(--border);
      color: var(--text-muted);
    }
    .btn-outline:hover { border-color: var(--accent); color: var(--text); }
    .btn-danger { background: var(--red); }
    .btn-danger:hover { background: #e05858; }
    .btn-add { background: var(--green); }
    .btn-add:hover { background: #2ab886; }

    .signal-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-top: 0.5rem;
    }
    .chip {
      padding: 0.25rem 0.6rem;
      border-radius: 999px;
      font-size: 0.75rem;
      cursor: pointer;
      border: 1px solid var(--border);
      color: var(--text-muted);
      transition: all 0.15s;
      user-select: none;
    }
    .chip.active {
      background: rgba(79,142,247,0.2);
      border-color: var(--accent);
      color: var(--accent);
    }
    .chip:hover { border-color: var(--accent); }

    /* Charts */
    .charts { display: flex; flex-direction: column; gap: 1rem; }
    .chart-container {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.5rem;
    }

    .status-bar {
      font-size: 0.78rem;
      color: var(--text-muted);
      margin-top: 0.5rem;
    }
    .error { color: var(--red); }

    .loading-spinner {
      display: inline-block;
      width: 14px; height: 14px;
      border: 2px solid var(--border);
      border-top-color: var(--accent);
      border-radius: 50%;
      animation: spin 0.6s linear infinite;
      vertical-align: middle;
      margin-right: 0.4rem;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>

<h1>Telemetry <span>Comparison</span></h1>

<!-- Session/Lap selection -->
<div class="panel">
  <div class="panel-title">Lap Selections (pick sessions and laps to overlay)</div>
  <div class="selections" id="selections"></div>
  <div style="margin-top: 0.75rem;">
    <button class="btn btn-sm btn-add" onclick="addSelection()">+ Add Lap</button>
    <button class="btn btn-sm" onclick="loadTelemetry()" id="btn-compare">Compare</button>
  </div>
</div>

<!-- Signal picker -->
<div class="panel">
  <div class="panel-title">Signals</div>
  <div class="signal-chips" id="signal-chips">
    <span class="chip active" data-signal="speedKmh">speedKmh</span>
    <span class="chip active" data-signal="gas">gas</span>
    <span class="chip active" data-signal="brake">brake</span>
    <span class="chip" data-signal="steerAngle">steerAngle</span>
    <span class="chip" data-signal="rpms">rpms</span>
    <span class="chip" data-signal="gear">gear</span>
    <span class="chip" data-signal="turboBoost">turboBoost</span>
    <span class="chip" data-signal="tyreTempFL">tyreTempFL</span>
    <span class="chip" data-signal="tyreTempFR">tyreTempFR</span>
    <span class="chip" data-signal="tyreTempRL">tyreTempRL</span>
    <span class="chip" data-signal="tyreTempRR">tyreTempRR</span>
    <span class="chip" data-signal="brakeTempFL">brakeTempFL</span>
    <span class="chip" data-signal="fuel">fuel</span>
    <span class="chip" data-signal="drs">drs</span>
    <span class="chip" data-signal="tc">tc</span>
    <span class="chip" data-signal="abs">abs</span>
  </div>
  <div style="margin-top: 0.5rem; display: flex; gap: 0.5rem; align-items: center;">
    <button class="btn btn-sm btn-outline" onclick="loadSignals()">Load all available signals</button>
  </div>
</div>

<!-- Charts -->
<div class="charts" id="charts"></div>

<div class="status-bar" id="status"></div>

<script>
const COLORS = [
  '#4f8ef7', '#f59e0b', '#34d399', '#f87171', '#a78bfa',
  '#ec4899', '#06b6d4', '#84cc16', '#f97316', '#8b5cf6'
];

let sessions = [];
let selectionCount = 0;

// --- Session/Lap selection ---

async function fetchSessions() {
  const res = await fetch('/api/sessions');
  const json = await res.json();
  sessions = json.sessions || [];
  return sessions;
}

async function fetchLaps(sessionId) {
  const res = await fetch(`/api/laps?session_id=${encodeURIComponent(sessionId)}`);
  const json = await res.json();
  return json.laps || [];
}

function sessionLabel(s) {
  const ts = s.session_id || '';
  const short = ts.substring(0, 19).replace('T', ' ');
  return `${short} | ${s.track} | ${s.carModel} | ${s.driver || 'NA'}`;
}

function addSelection() {
  const idx = selectionCount++;
  const color = COLORS[idx % COLORS.length];
  const row = document.createElement('div');
  row.className = 'selection-row';
  row.id = `sel-${idx}`;
  row.innerHTML = `
    <div class="color-dot" style="background:${color}"></div>
    <select id="session-${idx}" onchange="onSessionChange(${idx})">
      <option value="">Select session...</option>
      ${sessions.map(s => `<option value="${s.session_id}">${sessionLabel(s)}</option>`).join('')}
    </select>
    <select id="lap-${idx}" disabled>
      <option value="">Select lap...</option>
    </select>
    <button class="btn btn-sm btn-danger" onclick="removeSelection(${idx})">x</button>
  `;
  document.getElementById('selections').appendChild(row);
}

function removeSelection(idx) {
  const el = document.getElementById(`sel-${idx}`);
  if (el) el.remove();
}

async function onSessionChange(idx) {
  const sessionId = document.getElementById(`session-${idx}`).value;
  const lapSelect = document.getElementById(`lap-${idx}`);
  lapSelect.disabled = true;
  lapSelect.innerHTML = '<option value="">Loading...</option>';

  if (!sessionId) {
    lapSelect.innerHTML = '<option value="">Select lap...</option>';
    return;
  }

  const laps = await fetchLaps(sessionId);
  lapSelect.innerHTML = '<option value="">Select lap...</option>' +
    laps.map(l => `<option value="${l.lap}">Lap ${l.lap} (avg ${l.avg_speed} km/h, ${l.samples} pts)</option>`).join('');
  lapSelect.disabled = false;
}

// --- Signals ---

function getActiveSignals() {
  return Array.from(document.querySelectorAll('.chip.active')).map(c => c.dataset.signal);
}

document.getElementById('signal-chips').addEventListener('click', e => {
  if (e.target.classList.contains('chip')) {
    e.target.classList.toggle('active');
  }
});

async function loadSignals() {
  try {
    const res = await fetch('/api/signals');
    const json = await res.json();
    const container = document.getElementById('signal-chips');
    const active = new Set(getActiveSignals());
    container.innerHTML = '';
    for (const sig of json.signals) {
      const chip = document.createElement('span');
      chip.className = 'chip' + (active.has(sig) ? ' active' : '');
      chip.dataset.signal = sig;
      chip.textContent = sig;
      container.appendChild(chip);
    }
    // Re-bind click
    container.addEventListener('click', e => {
      if (e.target.classList.contains('chip')) e.target.classList.toggle('active');
    });
  } catch (e) {
    setStatus('Failed to load signals: ' + e.message, true);
  }
}

// --- Load & plot ---

function getSelections() {
  const rows = document.querySelectorAll('.selection-row');
  const result = [];
  rows.forEach((row, i) => {
    const selects = row.querySelectorAll('select');
    const sessionId = selects[0]?.value;
    const lap = selects[1]?.value;
    if (sessionId && lap) {
      const idx = parseInt(row.id.split('-')[1]);
      const session = sessions.find(s => s.session_id === sessionId);
      result.push({
        sessionId, lap: parseInt(lap),
        color: COLORS[idx % COLORS.length],
        label: `${session?.track || '?'} L${lap} (${session?.driver || 'NA'})`,
      });
    }
  });
  return result;
}

async function loadTelemetry() {
  const selections = getSelections();
  const signals = getActiveSignals();

  if (!selections.length) { setStatus('Select at least one session + lap', true); return; }
  if (!signals.length) { setStatus('Select at least one signal', true); return; }

  const btn = document.getElementById('btn-compare');
  btn.disabled = true;
  setStatus('<span class="loading-spinner"></span> Loading telemetry...');

  try {
    const allData = await Promise.all(
      selections.map(sel =>
        fetch(`/api/telemetry?session_id=${encodeURIComponent(sel.sessionId)}&lap=${sel.lap}&signals=${signals.join(',')}`)
          .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      )
    );

    const chartsDiv = document.getElementById('charts');
    chartsDiv.innerHTML = '';

    for (const signal of signals) {
      const container = document.createElement('div');
      container.className = 'chart-container';
      const plotDiv = document.createElement('div');
      container.appendChild(plotDiv);
      chartsDiv.appendChild(container);

      const traces = selections.map((sel, i) => {
        const d = allData[i].data;
        return {
          x: d.normalizedCarPosition,
          y: d[signal],
          type: 'scattergl',
          mode: 'lines',
          name: sel.label,
          line: { color: sel.color, width: 1.5 },
        };
      });

      Plotly.newPlot(plotDiv, traces, {
        title: { text: signal, font: { color: '#8892a4', size: 13 } },
        xaxis: {
          title: 'Track Position (0-1)',
          color: '#8892a4',
          gridcolor: '#2d3047',
          zerolinecolor: '#2d3047',
        },
        yaxis: {
          title: signal,
          color: '#8892a4',
          gridcolor: '#2d3047',
          zerolinecolor: '#2d3047',
        },
        paper_bgcolor: '#1a1d27',
        plot_bgcolor: '#1a1d27',
        font: { color: '#e2e8f0' },
        margin: { t: 40, r: 20, b: 50, l: 60 },
        legend: { orientation: 'h', y: -0.2 },
        height: 300,
      }, { responsive: true });
    }

    const totalPts = allData.reduce((sum, d) => sum + d.count, 0);
    setStatus(`Loaded ${totalPts.toLocaleString()} data points across ${selections.length} lap(s)`);
  } catch (e) {
    setStatus('Error: ' + e.message, true);
  } finally {
    btn.disabled = false;
  }
}

function setStatus(msg, isError = false) {
  const el = document.getElementById('status');
  el.innerHTML = msg;
  el.className = 'status-bar' + (isError ? ' error' : '');
}

// --- Init ---
(async () => {
  setStatus('<span class="loading-spinner"></span> Loading sessions...');
  try {
    await fetchSessions();
    addSelection();
    addSelection();
    setStatus(`Loaded ${sessions.length} sessions. Select sessions and laps, then click Compare.`);
  } catch (e) {
    setStatus('Failed to load sessions: ' + e.message, true);
  }
})();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
