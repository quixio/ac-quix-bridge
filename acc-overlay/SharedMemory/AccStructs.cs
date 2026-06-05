using System.Runtime.InteropServices;

namespace AccOverlay.SharedMemory;

// ============================================================================
//  ACC shared-memory struct layouts.
//
//  Assetto Corsa Competizione publishes three memory-mapped files under the
//  Local\ namespace, using the SAME names as the original AC but a RICHER,
//  DIFFERENT layout:
//      Local\acpmf_physics   -> SPageFilePhysics
//      Local\acpmf_graphics  -> SPageFileGraphic
//      Local\acpmf_static    -> SPageFileStatic
//
//  Field order is critical: the marshaller reads sequentially. We define only
//  the PREFIX of each page up to the last field we actually consume, which keeps
//  these layouts robust against fields Kunos append at the tail across patches.
//
//  All wide-char (wchar_t) fields are 2-byte UTF-16 on Windows -> CharSet.Unicode.
//  Pack = 4 matches AC/ACC's 4-byte packing.
//
//  Reference: ACC "Shared Memory Documentation" (Kunos) and the community
//  accSharedMemory headers. The original AC layout lives in
//  ../../ac-telemetry-source/models.py (note: ACC graphics adds the 60-car
//  carCoordinates/carID arrays the original AC struct does not have).
// ============================================================================

/// <summary>"No time set" sentinel ACC uses for lap-time int fields.</summary>
internal static class AccConst
{
    public const int IntMax = 2147483647;
}

/// <summary>
/// Prefix of SPageFilePhysics — only as far as accG, which is all the G-meter
/// needs. accG is the car acceleration in g: [0]=lateral, [1]=vertical,
/// [2]=longitudinal (configurable via appsettings in case a title flips signs).
/// </summary>
[StructLayout(LayoutKind.Sequential, Pack = 4)]
internal struct AccPhysics
{
    public int packetId;
    public float gas;
    public float brake;
    public float fuel;
    public int gear;
    public int rpms;
    public float steerAngle;
    public float speedKmh;

    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 3)]
    public float[] velocity;

    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 3)]
    public float[] accG;
}

/// <summary>
/// Prefix of SPageFileGraphic up to and including isValidLap. The lap-timing
/// fields we need (iBestTime, iDeltaLapTime, iEstimatedLapTime, isValidLap) sit
/// AFTER the 60-car coordinate/ID arrays, so the full intervening layout must be
/// reproduced exactly or the offsets shift and we read garbage.
/// </summary>
[StructLayout(LayoutKind.Sequential, Pack = 4, CharSet = CharSet.Unicode)]
internal struct AccGraphics
{
    public int packetId;
    public int status;            // 0=OFF 1=REPLAY 2=LIVE 3=PAUSE
    public int session;           // session type enum

    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string currentTime;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string lastTime;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string bestTime;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string split;

    public int completedLaps;
    public int position;
    public int iCurrentTime;      // current lap (ms)
    public int iLastTime;         // last lap (ms)
    public int iBestTime;         // session best lap (ms)
    public float sessionTimeLeft;
    public float distanceTraveled;
    public int isInPit;
    public int currentSectorIndex;
    public int lastSectorTime;
    public int numberOfLaps;

    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 33)] public string tyreCompound;

    public float replayTimeMultiplier;
    public float normalizedCarPosition;

    // --- ACC multi-car block (absent in original AC) ---
    public int activeCars;
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 180)] public float[] carCoordinates; // [60][3]
    [MarshalAs(UnmanagedType.ByValArray, SizeConst = 60)] public int[] carID;             // [60]
    public int playerCarID;

    public float penaltyTime;
    public int flag;
    public int penalty;
    public int idealLineOn;
    public int isInPitLane;
    public float surfaceGrip;
    public int mandatoryPitDone;
    public float windSpeed;
    public float windDirection;
    public int isSetupMenuVisible;
    public int mainDisplayIndex;
    public int secondaryDisplayIndex;
    public int TC;
    public int TCCut;
    public int EngineMap;
    public int ABS;
    public int fuelXLap;
    public int rainLights;
    public int flashingLights;
    public int lightsStage;
    public float exhaustTemperature;
    public int wiperLV;
    public int driverStintTotalTimeLeft;
    public int driverStintTimeLeft;
    public int rainTyres;
    public int sessionIndex;
    public int usedFuel;

    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string deltaLapTime;
    public int iDeltaLapTime;     // live delta vs best (ms; sign per isDeltaPositive)
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string estimatedLapTime;
    public int iEstimatedLapTime; // projected current lap (ms) == best + live delta
    public int isDeltaPositive;
    public int iSplit;
    public int isValidLap;        // 0 = lap invalidated (cut/off-track)
}

/// <summary>
/// Prefix of SPageFileStatic up to playerNick — enough to identify the driver
/// for the leaderboard "me" match.
/// </summary>
[StructLayout(LayoutKind.Sequential, Pack = 4, CharSet = CharSet.Unicode)]
internal struct AccStatic
{
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string smVersion;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 15)] public string acVersion;
    public int numberOfSessions;
    public int numCars;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 33)] public string carModel;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 33)] public string track;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 33)] public string playerName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 33)] public string playerSurname;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 33)] public string playerNick;
}
