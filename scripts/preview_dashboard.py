"""
Local preview server for the telemetry dashboard.

Serves the REAL telemetry-dashboard/static/index.html and pushes synthetic
telemetry over /ws that mimics a car running laps — so the F1-style layout,
gauges, shift lights, and the client-side sector/lap derivation can all be
exercised in a browser (or on a TV) without AC, the broker, or a Quix token.

Run via scripts/preview-dashboard.ps1 (sets up the venv + deps).
"""

import asyncio
import csv
import io
import json
import math
import os
import random
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "telemetry-dashboard" / "static"


def _load_env():
    """Populate unset env vars from telemetry-dashboard/.env or repo .env so the
    real Data Lake leaderboard can be tested locally."""
    for f in (REPO / "telemetry-dashboard" / ".env", REPO / ".env"):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v


_load_env()

app = FastAPI()


class Sim:
    """Drives a synthetic lap: ~24s base lap, 3 sectors, varying lap times."""

    BASE_LAP_S = 24.0

    def __init__(self):
        self.lap_start = time.perf_counter()
        self.lap_duration = self.BASE_LAP_S
        self.completed_laps = 0
        self.last_lap_ms = 0
        self.best_lap_ms = 0
        self.tyre = [70.0, 72.0, 75.0, 77.0]
        self.brake = [250.0, 255.0, 240.0, 245.0]

    def _new_lap(self):
        # Realistic lap times (~1:18-1:27), decoupled from the visual lap length,
        # with ~half the laps a new personal best so the leaderboard blink shows.
        if self.best_lap_ms == 0:
            self.last_lap_ms = 86000
        elif random.random() < 0.5:
            self.last_lap_ms = max(78000, self.best_lap_ms - random.randint(80, 600))
        else:
            self.last_lap_ms = self.best_lap_ms + random.randint(150, 1800)
        if self.best_lap_ms == 0 or self.last_lap_ms < self.best_lap_ms:
            self.best_lap_ms = self.last_lap_ms
        self.completed_laps += 1
        self.lap_start = time.perf_counter()
        self.lap_duration = self.BASE_LAP_S * random.uniform(0.97, 1.04)

    def frame(self) -> dict:
        now = time.perf_counter()
        t = now - self.lap_start
        if t >= self.lap_duration:
            self._new_lap()
            t = 0.0

        frac = t / self.lap_duration                      # 0..1 around the lap
        sector = min(int(frac * 3), 2)

        # Speed profile: a few "corners" via a sine, 90..315 km/h.
        wave = 0.5 + 0.5 * math.sin(frac * 2 * math.pi * 4 - math.pi / 2)
        speed = 90 + 225 * wave
        gear = max(1, min(7, int(speed / 45) + 1))
        rpm = int(4500 + 7000 * wave)
        gas = max(0.0, min(1.0, wave * 1.3))
        brake = max(0.0, min(1.0, (1 - wave) * 0.9 - 0.1))
        # Steering swings into the corners (radians); synced to the speed wave.
        steer = 0.5 * math.sin(frac * 2 * math.pi * 4)

        # Temps drift slowly with a little noise.
        for i in range(4):
            self.tyre[i] += (random.random() - 0.45) * 0.6
            self.tyre[i] = max(55, min(118, self.tyre[i]))
            self.brake[i] += (random.random() - 0.45) * 4
            self.brake[i] = max(180, min(720, self.brake[i]))

        return {
            "session_id": "preview",
            "speedKmh": round(speed, 1),
            "rpms": rpm,
            "gear": gear,
            "gas": round(gas, 3),
            "brake": round(brake, 3),
            "clutch": 1.0,
            "steerAngle": round(steer, 4),
            "fuel": round(58 - self.completed_laps * 1.7, 1),
            "turboBoost": round(wave * 1.1, 2),
            "airTemp": 26.0,
            "roadTemp": 34.0,
            "sessionType": "race",
            "completedLaps": self.completed_laps,
            "numberOfLaps": 10,
            "position": 1,
            "iCurrentTime": int(t * 1000),
            "iLastTime": self.last_lap_ms,
            "iBestTime": self.best_lap_ms,
            "currentSectorIndex": sector,
            "lastSectorTime": 0,
            "isInPitLane": 0,
            "isInPit": 0,
            "flag": "none",
            "drsAvailable": 1 if wave > 0.6 else 0,
            "drsEnabled": 1 if wave > 0.85 else 0,
            "tyreTempFL": round(self.tyre[0], 1), "tyreTempFR": round(self.tyre[1], 1),
            "tyreTempRL": round(self.tyre[2], 1), "tyreTempRR": round(self.tyre[3], 1),
            "brakeTempFL": round(self.brake[0]), "brakeTempFR": round(self.brake[1]),
            "brakeTempRL": round(self.brake[2]), "brakeTempRR": round(self.brake[3]),
            "wheelsPressureFL": round(26.5 + wave * 0.9, 1), "wheelsPressureFR": round(26.7 + wave * 1.1, 1),
            "wheelsPressureRL": round(26.2 + wave * 0.7, 1), "wheelsPressureRR": round(26.4 + wave * 0.8, 1),
            "accG_x": round(-steer * 2.5, 2),
            "accG_y": round((gas - brake) * 2.2, 2),
            "waterTemp": round(80 + wave * 8),
            "exhaustTemperature": round(320 + wave * 230),
            "fuelEstimatedLaps": round(max(0.0, 18 - self.completed_laps * 0.8), 1),
            "iDeltaLapTime": int(math.sin(t * 0.3) * 1800),
            "brakeBias": 0.62,
            "tc": 1 if (gas > 0.9 and speed < 120) else 0,
            "abs": 1 if brake > 0.6 else 0,
        }


@app.get("/health")
async def health():
    return {"status": "connected", "detail": "preview", "clients": 0}


DEFAULT_LEADERBOARD_SQL = (
    'SELECT driver AS name, MIN("iBestTime") AS ms '
    'FROM ac_telemetry '
    "WHERE \"iBestTime\" > 0 AND driver IS NOT NULL AND driver <> '' "
    "GROUP BY driver ORDER BY ms ASC LIMIT 10"
)


@app.get("/leaderboard")
async def leaderboard():
    # Proxy the real Data Lake if creds are configured (via .env); otherwise empty
    # so the UI keeps its placeholder board. Mirrors the deployed backend.
    url = os.environ.get("DATALAKE_API_URL") or os.environ.get("Quix__Lakehouse__Query__Url")
    token = os.environ.get("DATALAKE_API_TOKEN") or os.environ.get("Quix__Lakehouse__Query__AuthToken")
    if not url or not token:
        return {"rows": []}
    sql = os.environ.get("LEADERBOARD_SQL") or DEFAULT_LEADERBOARD_SQL
    if os.environ.get("DATALAKE_INSECURE_SSL", "").lower() in ("1", "true", "yes"):
        verify = False
    else:
        verify = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE") or True
    try:
        async with httpx.AsyncClient(timeout=20, verify=verify) as client:
            r = await client.post(
                f"{url.rstrip('/')}/query",
                content=sql.encode("utf-8"),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
            )
        r.raise_for_status()
        rows = []
        for row in csv.DictReader(io.StringIO(r.text)):
            name = (row.get("name") or "").strip()
            if not name or name.startswith("# ERROR"):
                continue
            try:
                rows.append({"name": name, "ms": int(float(row["ms"]))})
            except (TypeError, ValueError, KeyError):
                continue
        return {"rows": rows}
    except Exception as e:
        return {"rows": [], "error": str(e)}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "status", "status": "connected", "detail": "preview"}))
    # Simulate a driver resolved from ac-telemetry-config (override via PREVIEW_DRIVER).
    await websocket.send_text(json.dumps({"type": "driver", "name": os.environ.get("PREVIEW_DRIVER", "steve")}))
    sim = Sim()
    try:
        while True:
            await websocket.send_text(json.dumps(sim.frame()))
            await asyncio.sleep(1 / 30)  # 30 Hz
    except (WebSocketDisconnect, Exception):
        return


@app.get("/{full_path:path}")
async def root(full_path: str = ""):
    if full_path:
        try:
            candidate = (STATIC / full_path).resolve()
            if candidate.is_file() and STATIC.resolve() in candidate.parents:
                return FileResponse(str(candidate))
        except (OSError, ValueError):
            pass
    return FileResponse(str(STATIC / "index.html"))


if __name__ == "__main__":
    import uvicorn

    print("Preview dashboard -> http://localhost:8000  (Ctrl+C to stop)")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
