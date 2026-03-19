"""
Assetto Corsa Physics shared memory struct definition.

Matches the official AC shared memory layout for `acpmf_physics`.
Reference: https://assettocorsa.club/forum/index.php?threads/shared-memory-documentation.3352/

Field order is critical — ctypes reads sequentially from the memory-mapped region.
"""

import ctypes


class ACPhysics(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("packetId", ctypes.c_int32),
        ("gas", ctypes.c_float),
        ("brake", ctypes.c_float),
        ("fuel", ctypes.c_float),
        ("gear", ctypes.c_int32),
        ("rpms", ctypes.c_int32),
        ("steerAngle", ctypes.c_float),
        ("speedKmh", ctypes.c_float),
        ("velocity", ctypes.c_float * 3),
        ("accG", ctypes.c_float * 3),
        ("wheelSlip", ctypes.c_float * 4),
        ("wheelLoad", ctypes.c_float * 4),  # not used in older AC versions
        ("wheelsPressure", ctypes.c_float * 4),
        ("wheelAngularSpeed", ctypes.c_float * 4),
        ("tyreWear", ctypes.c_float * 4),  # not used in older AC versions
        ("tyreDirtyLevel", ctypes.c_float * 4),
        ("tyreCoreTemperature", ctypes.c_float * 4),
        ("camberRAD", ctypes.c_float * 4),
        ("suspensionTravel", ctypes.c_float * 4),
        ("drs", ctypes.c_float),
        ("tc", ctypes.c_float),
        ("heading", ctypes.c_float),
        ("pitch", ctypes.c_float),
        ("roll", ctypes.c_float),
        ("cgHeight", ctypes.c_float),  # not used in older AC versions
        ("carDamage", ctypes.c_float * 5),
        ("numberOfTyresOut", ctypes.c_int32),  # not used in older AC versions
        ("pitLimiterOn", ctypes.c_int32),
        ("abs", ctypes.c_float),
        ("kersCharge", ctypes.c_float),  # not used in older AC versions
        ("kersInput", ctypes.c_float),  # not used in older AC versions
        ("autoShifterOn", ctypes.c_int32),
        ("rideHeight", ctypes.c_float * 2),
        ("turboBoost", ctypes.c_float),
        ("ballast", ctypes.c_float),  # not used in older AC versions
        ("airDensity", ctypes.c_float),  # not used in older AC versions
        ("airTemp", ctypes.c_float),
        ("roadTemp", ctypes.c_float),
        ("localAngularVel", ctypes.c_float * 3),
        ("finalFF", ctypes.c_float),
        ("performanceMeter", ctypes.c_float),  # not used in older AC versions
        ("engineBrake", ctypes.c_int32),  # not used in older AC versions
        ("ersRecoveryLevel", ctypes.c_int32),  # not used in older AC versions
        ("ersPowerLevel", ctypes.c_int32),  # not used in older AC versions
        ("ersHeatCharging", ctypes.c_int32),  # not used in older AC versions
        ("ersIsCharging", ctypes.c_int32),  # not used in older AC versions
        ("kersCurrentKJ", ctypes.c_float),  # not used in older AC versions
        ("drsAvailable", ctypes.c_int32),  # not used in older AC versions
        ("drsEnabled", ctypes.c_int32),  # not used in older AC versions
        ("brakeTemp", ctypes.c_float * 4),
        ("clutch", ctypes.c_float),
        ("tyreTempI", ctypes.c_float * 4),  # not used in older AC versions
        ("tyreTempM", ctypes.c_float * 4),  # not used in older AC versions
        ("tyreTempO", ctypes.c_float * 4),  # not used in older AC versions
        ("isAIControlled", ctypes.c_int32),  # not used in older AC versions
        ("tyreContactPoint", (ctypes.c_float * 3) * 4),
        ("tyreContactNormal", (ctypes.c_float * 3) * 4),
        ("tyreContactHeading", (ctypes.c_float * 3) * 4),
        ("brakeBias", ctypes.c_float),
        ("localVelocity", ctypes.c_float * 3),
    ]
