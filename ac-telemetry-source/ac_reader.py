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
        Read current physics state and return a flat dict of all telemetry values.

        Arrays are unpacked into individual named keys (e.g. velocity_x/y/z,
        wheelSlipFL/FR/RL/RR) for easier downstream processing.
        """
        if self._mmap is None:
            raise RuntimeError("Shared memory not open. Call open() first.")

        self._mmap.seek(0)
        buf = self._mmap.read(SHM_SIZE)
        p = ACPhysics.from_buffer_copy(buf)

        WHEELS = ("FL", "FR", "RL", "RR")
        DAMAGE = ("front", "rear", "left", "right", "top")

        return {
            # Scalars
            "packetId": p.packetId,
            "gas": p.gas,
            "brake": p.brake,
            "fuel": p.fuel,
            "gear": p.gear,
            "rpms": p.rpms,
            "steerAngle": p.steerAngle,
            "speedKmh": p.speedKmh,
            "drs": p.drs,
            "tc": p.tc,
            "heading": p.heading,
            "pitch": p.pitch,
            "roll": p.roll,
            "cgHeight": p.cgHeight,
            "numberOfTyresOut": p.numberOfTyresOut,
            "pitLimiterOn": p.pitLimiterOn,
            "abs": p.abs,
            "kersCharge": p.kersCharge,
            "kersInput": p.kersInput,
            "autoShifterOn": p.autoShifterOn,
            "turboBoost": p.turboBoost,
            "ballast": p.ballast,
            "airDensity": p.airDensity,
            "airTemp": p.airTemp,
            "roadTemp": p.roadTemp,
            "finalFF": p.finalFF,
            "performanceMeter": p.performanceMeter,
            "engineBrake": p.engineBrake,
            "ersRecoveryLevel": p.ersRecoveryLevel,
            "ersPowerLevel": p.ersPowerLevel,
            "ersHeatCharging": p.ersHeatCharging,
            "ersIsCharging": p.ersIsCharging,
            "kersCurrentKJ": p.kersCurrentKJ,
            "drsAvailable": p.drsAvailable,
            "drsEnabled": p.drsEnabled,
            "clutch": p.clutch,
            "isAIControlled": p.isAIControlled,
            "brakeBias": p.brakeBias,

            # Vec3 arrays
            "velocity_x": p.velocity[0],
            "velocity_y": p.velocity[1],
            "velocity_z": p.velocity[2],
            "accG_x": p.accG[0],
            "accG_y": p.accG[1],
            "accG_z": p.accG[2],
            "localAngularVel_x": p.localAngularVel[0],
            "localAngularVel_y": p.localAngularVel[1],
            "localAngularVel_z": p.localAngularVel[2],
            "localVelocity_x": p.localVelocity[0],
            "localVelocity_y": p.localVelocity[1],
            "localVelocity_z": p.localVelocity[2],

            # Per-wheel arrays (FL, FR, RL, RR)
            **{f"wheelSlip{w}": p.wheelSlip[i] for i, w in enumerate(WHEELS)},
            **{f"wheelLoad{w}": p.wheelLoad[i] for i, w in enumerate(WHEELS)},
            **{f"wheelsPressure{w}": p.wheelsPressure[i] for i, w in enumerate(WHEELS)},
            **{f"wheelAngularSpeed{w}": p.wheelAngularSpeed[i] for i, w in enumerate(WHEELS)},
            **{f"tyreWear{w}": p.tyreWear[i] for i, w in enumerate(WHEELS)},
            **{f"tyreDirtyLevel{w}": p.tyreDirtyLevel[i] for i, w in enumerate(WHEELS)},
            **{f"tyreTemp{w}": p.tyreCoreTemperature[i] for i, w in enumerate(WHEELS)},
            **{f"camberRAD{w}": p.camberRAD[i] for i, w in enumerate(WHEELS)},
            **{f"suspensionTravel{w}": p.suspensionTravel[i] for i, w in enumerate(WHEELS)},
            **{f"brakeTemp{w}": p.brakeTemp[i] for i, w in enumerate(WHEELS)},
            **{f"tyreTempI{w}": p.tyreTempI[i] for i, w in enumerate(WHEELS)},
            **{f"tyreTempM{w}": p.tyreTempM[i] for i, w in enumerate(WHEELS)},
            **{f"tyreTempO{w}": p.tyreTempO[i] for i, w in enumerate(WHEELS)},

            # Per-wheel vec3 arrays (FL, FR, RL, RR × x, y, z)
            **{f"tyreContactPoint{w}_{a}": p.tyreContactPoint[i][j]
               for i, w in enumerate(WHEELS) for j, a in enumerate("xyz")},
            **{f"tyreContactNormal{w}_{a}": p.tyreContactNormal[i][j]
               for i, w in enumerate(WHEELS) for j, a in enumerate("xyz")},
            **{f"tyreContactHeading{w}_{a}": p.tyreContactHeading[i][j]
               for i, w in enumerate(WHEELS) for j, a in enumerate("xyz")},

            # Ride height (front, rear)
            "rideHeightFront": p.rideHeight[0],
            "rideHeightRear": p.rideHeight[1],

            # Car damage (5 zones)
            **{f"carDamage_{z}": p.carDamage[i] for i, z in enumerate(DAMAGE)},
        }
