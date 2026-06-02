"""
Windows shared memory reader for Assetto Corsa Competizione telemetry.

ACC reuses AC's three shared-memory region names (acpmf_physics, acpmf_graphics,
acpmf_static) but with extended struct layouts. See models.py for the struct
definitions ported from ACC Shared Memory Documentation v1.8.12.
"""

import ctypes
import logging
import mmap

from models import ACCGraphics, ACCPhysics, ACCStatic

logger = logging.getLogger(__name__)

SHM_PHYSICS = "Local\\acpmf_physics"
SHM_GRAPHICS = "Local\\acpmf_graphics"
SHM_STATIC = "Local\\acpmf_static"

SESSION_TYPES = {
    -1: "unknown", 0: "practice", 1: "qualify", 2: "race",
    3: "hotlap", 4: "time_attack", 5: "drift", 6: "drag",
    7: "hotstint", 8: "hotstint_superpole",
}
FLAG_TYPES = {
    0: "none", 1: "blue", 2: "yellow", 3: "black", 4: "white",
    5: "checkered", 6: "penalty", 7: "green", 8: "orange",
}
STATUS_TYPES = {0: "off", 1: "replay", 2: "live", 3: "pause"}
TRACK_GRIP_STATUS = {
    0: "green", 1: "fast", 2: "optimum", 3: "greasy",
    4: "damp", 5: "wet", 6: "flooded",
}
RAIN_INTENSITY = {
    0: "no_rain", 1: "drizzle", 2: "light_rain", 3: "medium_rain",
    4: "heavy_rain", 5: "thunderstorm",
}


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


class ACCReader:
    """Reads ACC data from all three Windows shared memory blocks."""

    def __init__(self):
        self._physics_mmap = None
        self._graphics_mmap = None
        self._static_mmap = None

    def open(self):
        self._physics_mmap = _open_shm(SHM_PHYSICS, ctypes.sizeof(ACCPhysics))
        self._graphics_mmap = _open_shm(SHM_GRAPHICS, ctypes.sizeof(ACCGraphics))
        self._static_mmap = _open_shm(SHM_STATIC, ctypes.sizeof(ACCStatic))

    def close(self):
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
        """Read physics + graphics and return a merged flat dict."""
        if not self.is_open:
            raise RuntimeError("Shared memory not open. Call open() first.")

        p = self._read_struct(self._physics_mmap, ACCPhysics)
        g = self._read_struct(self._graphics_mmap, ACCGraphics)

        WHEELS = ("FL", "FR", "RL", "RR")
        DAMAGE = ("front", "rear", "left", "right", "centre")

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
            "autoShifterOn": p.autoShifterOn,
            "turboBoost": p.turboBoost,
            "ballast": p.ballast,
            "airDensity": p.airDensity,
            "airTemp": p.airTemp,
            "roadTemp": p.roadTemp,
            "finalFF": p.finalFF,
            "clutch": p.clutch,
            "isAIControlled": p.isAIControlled,
            "brakeBias": p.brakeBias,
            "currentMaxRpm": p.currentMaxRpm,
            "tcinAction": p.tcinAction,
            "absInAction": p.absInAction,
            "waterTemp": p.waterTemp,
            "frontBrakeCompound": p.frontBrakeCompound,
            "rearBrakeCompound": p.rearBrakeCompound,
            "ignitionOn": p.ignitionOn,
            "starterEngineOn": p.starterEngineOn,
            "isEngineRunning": p.isEngineRunning,
            "kerbVibration": p.kerbVibration,
            "slipVibrations": p.slipVibrations,
            "gVibrations": p.gVibrations,
            "absVibrations": p.absVibrations,

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
            **{f"tyreCoreTemp{w}": p.tyreCoreTemperature[i] for i, w in enumerate(WHEELS)},
            **{f"tyreTemp{w}": p.tyreTemp[i] for i, w in enumerate(WHEELS)},
            **{f"camberRAD{w}": p.camberRAD[i] for i, w in enumerate(WHEELS)},
            **{f"suspensionTravel{w}": p.suspensionTravel[i] for i, w in enumerate(WHEELS)},
            **{f"suspensionDamage{w}": p.suspensionDamage[i] for i, w in enumerate(WHEELS)},
            **{f"brakeTemp{w}": p.brakeTemp[i] for i, w in enumerate(WHEELS)},
            **{f"brakePressure{w}": p.brakePressure[i] for i, w in enumerate(WHEELS)},
            **{f"padLife{w}": p.padLife[i] for i, w in enumerate(WHEELS)},
            **{f"discLife{w}": p.discLife[i] for i, w in enumerate(WHEELS)},
            **{f"slipRatio{w}": p.slipRatio[i] for i, w in enumerate(WHEELS)},
            **{f"slipAngle{w}": p.slipAngle[i] for i, w in enumerate(WHEELS)},

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
            "normalizedCarPosition": g.normalizedCarPosition,
            "activeCars": g.activeCars,
            "playerCarID": g.playerCarID,
            "penaltyTime": g.penaltyTime,
            "flag": FLAG_TYPES.get(g.flag, str(g.flag)),
            "penalty": g.penalty,
            "idealLineOn": g.idealLineOn,
            "isInPitLane": g.isInPitLane,
            "surfaceGrip": g.surfaceGrip,
            "mandatoryPitDone": g.mandatoryPitDone,
            "windSpeed": g.windSpeed,
            "windDirection": g.windDirection,
            "isSetupMenuVisible": g.isSetupMenuVisible,
            "mainDisplayIndex": g.mainDisplayIndex,
            "secondaryDisplayIndex": g.secondaryDisplyIndex,
            "TC": g.TC,
            "TCCUT": g.TCCUT,
            "EngineMap": g.EngineMap,
            "ABS": g.ABS,
            "fuelXLap": g.fuelXLap,
            "rainLights": g.rainLights,
            "flashingLights": g.flashingLights,
            "lightsStage": g.lightsStage,
            "exhaustTemperature": g.exhaustTemperature,
            "wiperLV": g.wiperLV,
            "driverStintTotalTimeLeft": g.driverStintTotalTimeLeft,
            "driverStintTimeLeft": g.driverStintTimeLeft,
            "rainTyres": g.rainTyres,
            "sessionIndex": g.sessionIndex,
            "usedFuel": g.usedFuel,
            "deltaLapTime": g.deltaLapTime.rstrip("\x00"),
            "iDeltaLapTime": g.iDeltaLapTime,
            "estimatedLapTime": g.estimatedLapTime.rstrip("\x00"),
            "iEstimatedLapTime": g.iEstimatedLapTime,
            "isDeltaPositive": g.isDeltaPositive,
            "iSplit": g.iSplit,
            "isValidLap": g.isValidLap,
            "fuelEstimatedLaps": g.fuelEstimatedLaps,
            "trackStatus": g.trackStatus.rstrip("\x00"),
            "missingMandatoryPits": g.missingMandatoryPits,
            "Clock": g.Clock,
            "directionLightsLeft": g.directionLightsLeft,
            "directionLightsRight": g.directionLightsRight,
            "GlobalYellow": g.GlobalYellow,
            "GlobalYellow1": g.GlobalYellow1,
            "GlobalYellow2": g.GlobalYellow2,
            "GlobalYellow3": g.GlobalYellow3,
            "GlobalWhite": g.GlobalWhite,
            "GlobalGreen": g.GlobalGreen,
            "GlobalChequered": g.GlobalChequered,
            "GlobalRed": g.GlobalRed,
            "mfdTyreSet": g.mfdTyreSet,
            "mfdFuelToAdd": g.mfdFuelToAdd,
            "mfdTyrePressureLF": g.mfdTyrePressureLF,
            "mfdTyrePressureRF": g.mfdTyrePressureRF,
            "mfdTyrePressureLR": g.mfdTyrePressureLR,
            "mfdTyrePressureRR": g.mfdTyrePressureRR,
            "trackGripStatus": TRACK_GRIP_STATUS.get(g.trackGripStatus, str(g.trackGripStatus)),
            "rainIntensity": RAIN_INTENSITY.get(g.rainIntensity, str(g.rainIntensity)),
            "rainIntensityIn10min": RAIN_INTENSITY.get(g.rainIntensityIn10min, str(g.rainIntensityIn10min)),
            "rainIntensityIn30min": RAIN_INTENSITY.get(g.rainIntensityIn30min, str(g.rainIntensityIn30min)),
            "currentTyreSet": g.currentTyreSet,
            "strategyTyreSet": g.strategyTyreSet,
            "gapAhead": g.gapAhead,
            "gapBehind": g.gapBehind,
        }

        return data

    def read_static(self) -> dict:
        """Read the static block and return a flat dict."""
        if not self.is_open:
            raise RuntimeError("Shared memory not open. Call open() first.")

        s = self._read_struct(self._static_mmap, ACCStatic)
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
            "maxRpm": s.maxRpm,
            "maxFuel": s.maxFuel,
            **{f"suspensionMaxTravel{w}": s.suspensionMaxTravel[i] for i, w in enumerate(WHEELS)},
            **{f"tyreRadius{w}": s.tyreRadius[i] for i, w in enumerate(WHEELS)},
            "maxTurboBoost": s.maxTurboBoost,
            "penaltiesEnabled": s.penaltiesEnabled,
            "aidFuelRate": s.aidFuelRate,
            "aidTireRate": s.aidTireRate,
            "aidMechanicalDamage": s.aidMechanicalDamage,
            "allowTyreBlankets": s.allowTyreBlankets,
            "aidStability": s.aidStability,
            "aidAutoClutch": s.aidAutoClutch,
            "aidAutoBlip": s.aidAutoBlip,
            "trackConfiguration": s.trackConfiguration.rstrip("\x00"),
            "pitWindowStart": s.pitWindowStart,
            "pitWindowEnd": s.pitWindowEnd,
            "isOnline": s.isOnline,
            "dryTyresName": s.dryTyresName.rstrip("\x00"),
            "wetTyresName": s.wetTyresName.rstrip("\x00"),
        }

    def get_session_key(self) -> str:
        """Return a string that uniquely identifies the current ACC session (car + track)."""
        if not self.is_open:
            return ""
        s = self._read_struct(self._static_mmap, ACCStatic)
        return f"{s.carModel.rstrip(chr(0))}|{s.track.rstrip(chr(0))}"
