using System.IO;
using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices;

namespace AccOverlay.SharedMemory;

/// <summary>
/// A flattened, UI-friendly snapshot of the bits of ACC shared memory the
/// overlay renders. Null <see cref="Connected"/> data means the mapped files
/// aren't present (ACC not running / not in a session yet).
/// </summary>
internal sealed class AccSnapshot
{
    public bool Connected;

    // G-force (g)
    public float AccLateral;
    public float AccLongitudinal;

    // Lap timing (ms; AccConst.IntMax == no time set)
    public int IBestTime = AccConst.IntMax;
    public int IEstimatedLapTime = AccConst.IntMax;
    public int IDeltaLapTime;
    public int ICurrentTime = AccConst.IntMax;
    public int ILastTime = AccConst.IntMax;
    public bool LapValid = true;
    public int Position;

    // Identity
    public string DriverName = "";
}

/// <summary>
/// Reads ACC's three memory-mapped pages and projects them into an
/// <see cref="AccSnapshot"/>. Open is lazy and resilient: if the maps don't
/// exist yet it simply reports "not connected" and retries on the next poll, so
/// the overlay can be launched before (or alongside) the game.
/// </summary>
internal sealed class AccTelemetry : IDisposable
{
    private const string PhysicsName = "Local\\acpmf_physics";
    private const string GraphicsName = "Local\\acpmf_graphics";
    private const string StaticName = "Local\\acpmf_static";

    private MemoryMappedFile? _physics;
    private MemoryMappedFile? _graphics;
    private MemoryMappedFile? _static;

    private readonly int _latIdx;
    private readonly int _lonIdx;
    private readonly float _latSign;
    private readonly float _lonSign;

    public AccTelemetry(int lateralIndex, int longitudinalIndex, bool invertLateral, bool invertLongitudinal)
    {
        _latIdx = Math.Clamp(lateralIndex, 0, 2);
        _lonIdx = Math.Clamp(longitudinalIndex, 0, 2);
        _latSign = invertLateral ? -1f : 1f;
        _lonSign = invertLongitudinal ? -1f : 1f;
    }

    /// <summary>Read the latest frame. Never throws; returns Connected=false on any failure.</summary>
    public AccSnapshot Read()
    {
        var snap = new AccSnapshot();
        try
        {
            if (!EnsureOpen())
                return snap;

            var phys = ReadStruct<AccPhysics>(_physics!);
            var gfx = ReadStruct<AccGraphics>(_graphics!);
            var stat = ReadStruct<AccStatic>(_static!);

            float[] g = phys.accG ?? new float[3];
            snap.AccLateral = g.Length > _latIdx ? g[_latIdx] * _latSign : 0f;
            snap.AccLongitudinal = g.Length > _lonIdx ? g[_lonIdx] * _lonSign : 0f;

            snap.IBestTime = gfx.iBestTime;
            snap.IEstimatedLapTime = gfx.iEstimatedLapTime;
            snap.IDeltaLapTime = gfx.iDeltaLapTime;
            snap.ICurrentTime = gfx.iCurrentTime;
            snap.ILastTime = gfx.iLastTime;
            snap.LapValid = gfx.isValidLap != 0;
            snap.Position = gfx.position;

            snap.DriverName = BuildName(stat);
            snap.Connected = true;
        }
        catch
        {
            // ACC closed mid-read, or a page vanished. Drop handles so the next
            // poll re-opens cleanly, and report a disconnected frame.
            Reset();
        }
        return snap;
    }

    private static string BuildName(AccStatic s)
    {
        string first = (s.playerName ?? "").Trim();
        string last = (s.playerSurname ?? "").Trim();
        string full = (first + " " + last).Trim();
        if (full.Length > 0) return full;
        return (s.playerNick ?? "").Trim();
    }

    private bool EnsureOpen()
    {
        _physics ??= TryOpen(PhysicsName);
        _graphics ??= TryOpen(GraphicsName);
        _static ??= TryOpen(StaticName);
        return _physics != null && _graphics != null && _static != null;
    }

    private static MemoryMappedFile? TryOpen(string name)
    {
        try
        {
            return MemoryMappedFile.OpenExisting(name, MemoryMappedFileRights.Read);
        }
        catch (FileNotFoundException)
        {
            return null; // game not running / page not created yet
        }
    }

    // MemoryMappedViewAccessor.Read<T> rejects structs with marshalled string /
    // array fields, so copy the raw bytes and PtrToStructure them — that honours
    // the [MarshalAs] attributes (ByValTStr / ByValArray).
    private static T ReadStruct<T>(MemoryMappedFile mmf) where T : struct
    {
        int size = Marshal.SizeOf<T>();
        var buffer = new byte[size];
        using (var acc = mmf.CreateViewAccessor(0, size, MemoryMappedFileAccess.Read))
        {
            acc.ReadArray(0, buffer, 0, size);
        }

        var handle = GCHandle.Alloc(buffer, GCHandleType.Pinned);
        try
        {
            return Marshal.PtrToStructure<T>(handle.AddrOfPinnedObject());
        }
        finally
        {
            handle.Free();
        }
    }

    private void Reset()
    {
        _physics?.Dispose();
        _graphics?.Dispose();
        _static?.Dispose();
        _physics = _graphics = _static = null;
    }

    public void Dispose() => Reset();
}
