"""
Mock AC shared memory reader for testing without Assetto Corsa.

Simulates a session lifecycle with configurable lap duration
(default 30s per lap via MOCK_LAP_DURATION_S env var):

  1. OFF for 3 seconds
  2. LIVE — lap 0 for LAP_DURATION seconds
  3. LIVE — lap 1 for LAP_DURATION seconds
  4. PAUSE for 5 seconds
  5. LIVE — lap 1 resume for LAP_DURATION seconds
  6. LIVE — lap 2 for LAP_DURATION seconds
  7. OFF (session end)
  8. Repeat from step 1
"""

import logging
import os
import time

logger = logging.getLogger(__name__)


class ACGraphicsReaderMock:
    """Simulates AC graphics/static data for testing the video pipeline."""

    def __init__(self):
        self._open = False
        self._start_time = None
        self._cycle_start = None
        self._lap_dur = int(os.environ.get("MOCK_LAP_DURATION_S", "30"))

    def open(self):
        self._open = True
        self._start_time = time.time()
        self._cycle_start = time.time()
        d = self._lap_dur
        total = 3 + d + d + 5 + d + d + 3
        logger.info(
            "Mock AC reader opened — lap duration %ds, full cycle %ds",
            d, total,
        )

    def close(self):
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def _elapsed(self) -> float:
        return time.time() - self._cycle_start

    def read_graphics(self) -> dict:
        """Return simulated graphics state based on elapsed time."""
        if not self._open:
            raise RuntimeError("Not open")

        d = self._lap_dur
        t = self._elapsed()

        # Timeline (seconds into cycle):
        #  0       .. 3          : OFF
        #  3       .. 3+d        : LIVE, lap 0
        #  3+d     .. 3+2d       : LIVE, lap 1
        #  3+2d    .. 3+2d+5     : PAUSE
        #  3+2d+5  .. 3+3d+5     : LIVE, lap 1 (resume)
        #  3+3d+5  .. 3+4d+5     : LIVE, lap 2
        #  3+4d+5  .. 3+4d+8     : OFF
        t_off1 = 3
        t_lap0 = t_off1 + d
        t_lap1 = t_lap0 + d
        t_pause = t_lap1 + 5
        t_resume = t_pause + d
        t_lap2 = t_resume + d
        t_end = t_lap2 + 3

        if t >= t_end:
            self._cycle_start = time.time()
            t = 0

        if t < t_off1:
            status, laps, current_time = "off", 0, 0
        elif t < t_lap0:
            status, laps = "live", 0
            current_time = int((t - t_off1) * 1000)
        elif t < t_lap1:
            status, laps = "live", 1
            current_time = int((t - t_lap0) * 1000)
        elif t < t_pause:
            status, laps = "pause", 1
            current_time = int(d * 1000)
        elif t < t_resume:
            status, laps = "live", 1
            current_time = int((d + t - t_pause) * 1000)
        elif t < t_lap2:
            status, laps = "live", 2
            current_time = int((t - t_resume) * 1000)
        else:
            status, laps, current_time = "off", 2, 0

        return {
            "status": status,
            "sessionType": "hotlap",
            "completedLaps": laps,
            "iCurrentTime": current_time,
            "flag": "none",
            "normalizedCarPosition": self._norm_pos(laps, current_time, status),
            "isInPit": False,
            "isInPitLane": False,
        }

    def _norm_pos(self, laps: int, current_time: int, status: str) -> float:
        """Monotonic 0->1 ramp per lap so sidecar JSON / Telemetry Explorer
        sync can be exercised end-to-end in mock mode.

        Lap 0 and lap 2 each take MOCK_LAP_DURATION_S seconds of LIVE time.
        Lap 1 is split by a mid-lap pause so its full LIVE span is 2x that.
        Position is held during PAUSE."""
        if status not in ("live", "pause"):
            return 0.0
        d_ms = self._lap_dur * 1000
        full_dur = (2 * d_ms) if laps == 1 else d_ms
        if full_dur <= 0:
            return 0.0
        return max(0.0, min(1.0, current_time / full_dur))

    def read_static(self) -> dict:
        return {
            "carModel": "mock_car_ks_ferrari_488",
            "track": "mock_track_monza",
        }
