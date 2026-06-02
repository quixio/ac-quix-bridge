"""
Windows shared memory reader for Assetto Corsa Competizione telemetry.

ACC reuses AC's three shared-memory region names (acpmf_physics, acpmf_graphics,
acpmf_static) but with extended struct layouts. See models.py for the struct
definitions ported from ACC Shared Memory Documentation v1.8.12.
"""

import ctypes
import logging
import mmap
import sys

from models import ACCGraphics, ACCPhysics, ACCStatic

logger = logging.getLogger(__name__)

SHM_PHYSICS = "Local\\acpmf_physics"
SHM_GRAPHICS = "Local\\acpmf_graphics"
SHM_STATIC = "Local\\acpmf_static"

# Win32 OpenFileMappingW probe: opens a named region ONLY if it exists.
# Python's mmap.mmap(-1, size, name, ACCESS_READ) auto-creates a zero-filled
# region when the name is missing — that hides "ACC not running" and risks
# size-mismatch conflicts when ACC starts later. Probing with OpenFileMappingW
# first lets us fail fast and never create the region ourselves.
if sys.platform == "win32":
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _OpenFileMappingW = _kernel32.OpenFileMappingW
    _OpenFileMappingW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _OpenFileMappingW.restype = wintypes.HANDLE
    _CloseHandle = _kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL
    _FILE_MAP_READ = 0x0004


def _probe_shm_exists(name: str) -> None:
    """Raise FileNotFoundError if the named region does not already exist."""
    if sys.platform != "win32":
        return  # dev only; real run is always Windows
    handle = _OpenFileMappingW(_FILE_MAP_READ, False, name)
    if not handle:
        raise FileNotFoundError(
            f"Shared memory '{name}' not found — is ACC running and in a session?"
        )
    _CloseHandle(handle)

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


def _player_xyz(g) -> tuple[float, float, float]:
    """Player car coordinates from the multi-car array.

    ACC stores positions for up to 60 cars; the player slot is found by
    matching `playerCarID` against the `carID[]` array. Falls back to (0,0,0)
    if the ID can't be located (offline/hotlap with no AI typically still has
    activeCars=1 and carID[0]=playerCarID).
    """
    pid = g.playerCarID
    n = max(1, min(g.activeCars, 60))
    for i in range(n):
        if g.carID[i] == pid:
            c = g.carCoordinates[i]
            return c[0], c[1], c[2]
    return 0.0, 0.0, 0.0


def _open_shm(name: str, size: int) -> mmap.mmap:
    """Open an existing named shared memory region. Raises FileNotFoundError if missing."""
    _probe_shm_exists(name)
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
        # AC's per-wheel damage tuple — ACC PDF labels the 5th index "centre"
        # but we keep "top" for AC column-name parity in the lake.
        DAMAGE = ("front", "rear", "left", "right", "top")

        # Player coordinates lifted from the multi-car array for AC parity
        # (AC writes a single carCoordinates[3]; ACC has carCoordinates[60][3]).
        px, py, pz = _player_xyz(g)

        data = {
            # ===== AC-parity field set (same key names as ac_reader.py) =====

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
            "carCoordinates_x": px,
            "carCoordinates_y": py,
            "carCoordinates_z": pz,
            "penaltyTime": g.penaltyTime,
            "flag": FLAG_TYPES.get(g.flag, str(g.flag)),
            "idealLineOn": g.idealLineOn,
            "isInPitLane": g.isInPitLane,
            "surfaceGrip": g.surfaceGrip,
            "mandatoryPitDone": g.mandatoryPitDone,

            # ===== ACC-only extras (no AC equivalent) =====

            # Physics extras
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
            **{f"suspensionDamage{w}": p.suspensionDamage[i] for i, w in enumerate(WHEELS)},
            **{f"brakePressure{w}": p.brakePressure[i] for i, w in enumerate(WHEELS)},
            **{f"padLife{w}": p.padLife[i] for i, w in enumerate(WHEELS)},
            **{f"discLife{w}": p.discLife[i] for i, w in enumerate(WHEELS)},
            **{f"slipRatio{w}": p.slipRatio[i] for i, w in enumerate(WHEELS)},
            **{f"slipAngle{w}": p.slipAngle[i] for i, w in enumerate(WHEELS)},

            # Graphics extras
            "activeCars": g.activeCars,
            "playerCarID": g.playerCarID,
            "penalty": g.penalty,
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
            # ===== AC-parity field set (same key names as ac_reader.py) =====
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
            "aidAllowTyreBlankets": s.allowTyreBlankets,
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
            # ===== ACC-only extras =====
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
