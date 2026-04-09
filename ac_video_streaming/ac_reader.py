"""
Lightweight AC shared memory reader for video recording.

Only opens graphics and static memory blocks (no physics needed).
Provides status, session, and lap detection for recording control.
"""

import ctypes
import mmap
import logging

from models import ACGraphics, ACStatic

logger = logging.getLogger(__name__)

SHM_GRAPHICS = "Local\\acpmf_graphics"
SHM_STATIC = "Local\\acpmf_static"

STATUS_TYPES = {0: "off", 1: "replay", 2: "live", 3: "pause"}
SESSION_TYPES = {
    -1: "unknown", 0: "practice", 1: "qualify", 2: "race",
    3: "hotlap", 4: "time_attack", 5: "drift", 6: "drag",
}
FLAG_TYPES = {
    0: "none", 1: "blue", 2: "yellow", 3: "black",
    4: "white", 5: "checkered", 6: "penalty",
}


def _open_shm(name: str, size: int) -> mmap.mmap:
    try:
        m = mmap.mmap(-1, size, name, access=mmap.ACCESS_READ)
        logger.info("Opened shared memory '%s' (%d bytes)", name, size)
        return m
    except Exception as e:
        raise FileNotFoundError(
            f"Could not open shared memory '{name}': {e}"
        ) from e


class ACGraphicsReader:
    """Reads AC graphics and static shared memory for session/status detection."""

    def __init__(self):
        self._graphics_mmap = None
        self._static_mmap = None

    def open(self):
        self._graphics_mmap = _open_shm(SHM_GRAPHICS, ctypes.sizeof(ACGraphics))
        self._static_mmap = _open_shm(SHM_STATIC, ctypes.sizeof(ACStatic))

    def close(self):
        for attr in ("_graphics_mmap", "_static_mmap"):
            m = getattr(self, attr)
            if m is not None:
                m.close()
                setattr(self, attr, None)

    @property
    def is_open(self) -> bool:
        return self._graphics_mmap is not None

    def _read_struct(self, m: mmap.mmap, struct_cls):
        m.seek(0)
        buf = m.read(ctypes.sizeof(struct_cls))
        return struct_cls.from_buffer_copy(buf)

    def read_graphics(self) -> dict:
        """Read graphics block — status, session, laps, flags."""
        if not self.is_open:
            raise RuntimeError("Shared memory not open. Call open() first.")
        g = self._read_struct(self._graphics_mmap, ACGraphics)
        return {
            "status": STATUS_TYPES.get(g.status, str(g.status)),
            "sessionType": SESSION_TYPES.get(g.session, str(g.session)),
            "completedLaps": g.completedLaps,
            "iCurrentTime": g.iCurrentTime,
            "flag": FLAG_TYPES.get(g.flag, str(g.flag)),
            "normalizedCarPosition": g.normalizedCarPosition,
        }

    def read_static(self) -> dict:
        """Read static block — car model, track (changes only on session load)."""
        if not self.is_open:
            raise RuntimeError("Shared memory not open. Call open() first.")
        s = self._read_struct(self._static_mmap, ACStatic)
        return {
            "carModel": s.carModel.rstrip("\x00"),
            "track": s.track.rstrip("\x00"),
        }
