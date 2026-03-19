"""
Windows shared memory reader for Assetto Corsa physics telemetry.

Opens the `Local\\acpmf_physics` memory-mapped file and reads it into
the ACPhysics ctypes struct.
"""

import ctypes
import mmap
import logging

from models import ACPhysics

logger = logging.getLogger(__name__)

SHM_NAME = "Local\\acpmf_physics"
SHM_SIZE = ctypes.sizeof(ACPhysics)


class ACReader:
    """Reads Assetto Corsa physics data from Windows shared memory."""

    def __init__(self):
        self._mmap = None

    def open(self):
        """Open the shared memory region. Raises FileNotFoundError if AC is not running."""
        try:
            self._mmap = mmap.mmap(-1, SHM_SIZE, SHM_NAME, access=mmap.ACCESS_READ)
            logger.info("Opened AC shared memory (%d bytes)", SHM_SIZE)
        except Exception as e:
            self._mmap = None
            raise FileNotFoundError(
                f"Could not open AC shared memory '{SHM_NAME}': {e}"
            ) from e

    def close(self):
        """Close the shared memory region."""
        if self._mmap is not None:
            self._mmap.close()
            self._mmap = None

    @property
    def is_open(self) -> bool:
        return self._mmap is not None

    def read(self) -> dict:
        """
        Read current physics state and return a flat dict of telemetry values.

        Returns dict with keys:
            speedKmh, gear, accG_x, accG_y, accG_z,
            tyreTempFL, tyreTempFR, tyreTempRL, tyreTempRR,
            brakeTempFL, brakeTempFR, brakeTempRL, brakeTempRR
        """
        if self._mmap is None:
            raise RuntimeError("Shared memory not open. Call open() first.")

        self._mmap.seek(0)
        buf = self._mmap.read(SHM_SIZE)
        physics = ACPhysics.from_buffer_copy(buf)

        return {
            "speedKmh": physics.speedKmh,
            "gear": physics.gear,
            "accG_x": physics.accG[0],
            "accG_y": physics.accG[1],
            "accG_z": physics.accG[2],
            "tyreTempFL": physics.tyreCoreTemperature[0],
            "tyreTempFR": physics.tyreCoreTemperature[1],
            "tyreTempRL": physics.tyreCoreTemperature[2],
            "tyreTempRR": physics.tyreCoreTemperature[3],
            "brakeTempFL": physics.brakeTemp[0],
            "brakeTempFR": physics.brakeTemp[1],
            "brakeTempRL": physics.brakeTemp[2],
            "brakeTempRR": physics.brakeTemp[3],
        }
