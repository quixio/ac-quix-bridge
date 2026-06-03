"""
Local preview server for the telemetry dashboard.

Serves the REAL telemetry-dashboard/static/index.html and pushes synthetic
telemetry over /ws that mimics a car running laps — so the F1-style layout,
gauges, shift lights, and the client-side sector/lap derivation can all be
exercised in a browser (or on a TV) without AC, the broker, or a Quix token.

Run via scripts/preview-dashboard.ps1 (sets up the venv + deps).
"""

import asyncio
import json
import math
import random
import time
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

STATIC = Path(__file__).resolve().parent.parent / "telemetry-dashboard" / "static"

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
        }


@app.get("/health")
async def health():
    return {"status": "connected", "detail": "preview", "clients": 0}


@app.get("/leaderboard")
async def leaderboard():
    # Preview has no Data Lake; return empty so the UI keeps its placeholder board.
    return {"rows": []}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "status", "status": "connected", "detail": "preview"}))
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
