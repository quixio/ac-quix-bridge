"""
Windows shared memory reader for Assetto Corsa telemetry.

Opens all three AC shared memory blocks (physics, graphics, static)
and provides methods to read each into flat dicts.
"""

import ctypes
import mmap
import logging

from models import ACPhysics, ACGraphics, ACStatic

logger = logging.getLogger(__name__)

SHM_PHYSICS = "Local\\acpmf_physics"
SHM_GRAPHICS = "Local\\acpmf_graphics"
SHM_STATIC = "Local\\acpmf_static"

SESSION_TYPES = {
    -1: "unknown", 0: "practice", 1: "qualify", 2: "race",
    3: "hotlap", 4: "time_attack", 5: "drift", 6: "drag",
}
FLAG_TYPES = {
    0: "none", 1: "blue", 2: "yellow", 3: "black",
    4: "white", 5: "checkered", 6: "penalty",
}
STATUS_TYPES = {0: "off", 1: "replay", 2: "live", 3: "pause"}


def _open_shm(name: str, size: int) -> mmap.mmap:
    """Open a named shared memory region. Raises FileNotFoundError on failure."""
    try:
        m = mmap.mmap(-1, size, name, access=mmap.ACCESS_READ)
        logger.info("Opened shared memory '%s' (%d bytes)", name, size)
        return m
    except Exception as e:
        raise FileNotFoundError(
            f"Could not open shared memory '{name}': {e}"
        ) from e


class ACReader:
    """Reads Assetto Corsa data from all three Windows shared memory blocks."""

    def __init__(self):
        self._physics_mmap = None
        self._graphics_mmap = None
        self._static_mmap = None

    def open(self):
        """Open all shared memory regions."""
        self._physics_mmap = _open_shm(SHM_PHYSICS, ctypes.sizeof(ACPhysics))
        self._graphics_mmap = _open_shm(SHM_GRAPHICS, ctypes.sizeof(ACGraphics))
        self._static_mmap = _open_shm(SHM_STATIC, ctypes.sizeof(ACStatic))

    def close(self):
        """Close all shared memory regions."""
        for attr in ("_physics_mmap", "_graphics_mmap", "_static_mmap"):
            m = getattr(self, attr)
            if m is not None:
                m.close()
                setattr(self, attr, None)

    @property
    def is_open(self) -> bool:
        return self._physics_mmap is not None

    def _read_struct(self, m: mmap.mmap, struct_cls):
        m.seek(0)
        buf = m.read(ctypes.sizeof(struct_cls))
        return struct_cls.from_buffer_copy(buf)

    def read_physics_and_graphics(self) -> dict:
        """
        Read physics + graphics and return a merged flat dict.
        This is the high-frequency data produced every tick.
        """
        if not self.is_open:
            raise RuntimeError("Shared memory not open. Call open() first.")

        p = self._read_struct(self._physics_mmap, ACPhysics)
        g = self._read_struct(self._graphics_mmap, ACGraphics)

        WHEELS = ("FL", "FR", "RL", "RR")
        DAMAGE = ("front", "rear", "left", "right", "top")

        data = {
            # --- Physics scalars ---
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

            # Physics vec3
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

            # Physics per-wheel
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

            # Physics per-wheel vec3
            **{f"tyreContactPoint{w}_{a}": p.tyreContactPoint[i][j]
               for i, w in enumerate(WHEELS) for j, a in enumerate("xyz")},
            **{f"tyreContactNormal{w}_{a}": p.tyreContactNormal[i][j]
               for i, w in enumerate(WHEELS) for j, a in enumerate("xyz")},
            **{f"tyreContactHeading{w}_{a}": p.tyreContactHeading[i][j]
               for i, w in enumerate(WHEELS) for j, a in enumerate("xyz")},

            # Physics ride height & damage
            "rideHeightFront": p.rideHeight[0],
            "rideHeightRear": p.rideHeight[1],
            **{f"carDamage_{z}": p.carDamage[i] for i, z in enumerate(DAMAGE)},

            # --- Graphics ---
            "status": STATUS_TYPES.get(g.status, str(g.status)),
            "sessionType": SESSION_TYPES.get(g.session, str(g.session)),
            "currentTime": g.currentTime.rstrip("\x00"),
            "lastTime": g.lastTime.rstrip("\x00"),
            "bestTime": g.bestTime.rstrip("\x00"),
            "split": g.split.rstrip("\x00"),
            "completedLaps": g.completedLaps,
            "position": g.position,
            "iCurrentTime": g.iCurrentTime,
            "iLastTime": g.iLastTime,
            "iBestTime": g.iBestTime,
            "sessionTimeLeft": g.sessionTimeLeft,
            "distanceTraveled": g.distanceTraveled,
            "isInPit": g.isInPit,
            "currentSectorIndex": g.currentSectorIndex,
            "lastSectorTime": g.lastSectorTime,
            "numberOfLaps": g.numberOfLaps,
            "tyreCompound": g.tyreCompound.rstrip("\x00"),
            "replayTimeMultiplier": g.replayTimeMultiplier,
            "normalizedCarPosition": g.normalizedCarPosition,
            "carCoordinates_x": g.carCoordinates[0],
            "carCoordinates_y": g.carCoordinates[1],
            "carCoordinates_z": g.carCoordinates[2],
            "penaltyTime": g.penaltyTime,
            "flag": FLAG_TYPES.get(g.flag, str(g.flag)),
            "idealLineOn": g.idealLineOn,
            "isInPitLane": g.isInPitLane,
            "surfaceGrip": g.surfaceGrip,
            "mandatoryPitDone": g.mandatoryPitDone,
        }

        return data

    def read_static(self) -> dict:
        """
        Read the static block and return a flat dict.
        This data changes only on session load.
        """
        if not self.is_open:
            raise RuntimeError("Shared memory not open. Call open() first.")

        s = self._read_struct(self._static_mmap, ACStatic)

        WHEELS = ("FL", "FR", "RL", "RR")

        return {
            "smVersion": s.smVersion.rstrip("\x00"),
            "acVersion": s.acVersion.rstrip("\x00"),
            "numberOfSessions": s.numberOfSessions,
            "numCars": s.numCars,
            "carModel": s.carModel.rstrip("\x00"),
            "track": s.track.rstrip("\x00"),
            "playerName": s.playerName.rstrip("\x00"),
            "playerSurname": s.playerSurname.rstrip("\x00"),
            "playerNick": s.playerNick.rstrip("\x00"),
            "sectorCount": s.sectorCount,
            "maxTorque": s.maxTorque,
            "maxPower": s.maxPower,
            "maxRpm": s.maxRpm,
            "maxFuel": s.maxFuel,
            **{f"suspensionMaxTravel{w}": s.suspensionMaxTravel[i] for i, w in enumerate(WHEELS)},
            **{f"tyreRadius{w}": s.tyreRadius[i] for i, w in enumerate(WHEELS)},
            "maxTurboBoost": s.maxTurboBoost,
            "penaltiesEnabled": s.penaltiesEnabled,
            "aidFuelRate": s.aidFuelRate,
            "aidTireRate": s.aidTireRate,
            "aidMechanicalDamage": s.aidMechanicalDamage,
            "aidAllowTyreBlankets": s.aidAllowTyreBlankets,
            "aidStability": s.aidStability,
            "aidAutoClutch": s.aidAutoClutch,
            "aidAutoBlip": s.aidAutoBlip,
            "hasDRS": s.hasDRS,
            "hasERS": s.hasERS,
            "hasKERS": s.hasKERS,
            "kersMaxJoules": s.kersMaxJoules,
            "engineBrakeSettingsCount": s.engineBrakeSettingsCount,
            "ersPowerControllerCount": s.ersPowerControllerCount,
            "trackSplineLength": s.trackSplineLength,
            "trackConfiguration": s.trackConfiguration.rstrip("\x00"),
            "ersMaxJ": s.ersMaxJ,
            "isTimedRace": s.isTimedRace,
            "hasExtraLap": s.hasExtraLap,
            "carSkin": s.carSkin.rstrip("\x00"),
            "reversedGridPositions": s.reversedGridPositions,
            "pitWindowStart": s.pitWindowStart,
            "pitWindowEnd": s.pitWindowEnd,
        }

    def get_session_key(self) -> str:
        """Return a string that uniquely identifies the current AC session (car + track)."""
        if not self.is_open:
            return ""
        s = self._read_struct(self._static_mmap, ACStatic)
        return f"{s.carModel.rstrip(chr(0))}|{s.track.rstrip(chr(0))}"
