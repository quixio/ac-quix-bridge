using AccOverlay.SharedMemory;

namespace AccOverlay.Services;

/// <summary>One displayed leaderboard line.</summary>
internal sealed class LbRow
{
    public int Position;     // absolute rank (1-based)
    public string Name = "";
    public int Ms;
    public bool IsMe;        // belongs to the local player
    public bool IsLive;      // the in-progress (projected) row
}

/// <summary>Result of a ranking pass: where the player sits + a slice to show.</summary>
internal sealed class LbResult
{
    public int PredictedPosition;   // 0 = unknown
    public int PredictedMs;         // the projected lap used for ranking (ms)
    public List<LbRow> Window = new();
}

/// <summary>
/// Computes the player's predicted leaderboard position, mirroring the scichart
/// dashboard's logic:
///   * baseline = all-time best lap per driver (from /leaderboard),
///   * the player's session best (iBestTime) folds into their baseline row when faster,
///   * a LIVE row = the projected lap (iEstimatedLapTime) is inserted while the
///     current lap is valid and a projection exists; it re-sorts as the lap evolves,
///   * position = the rank of that live row (falling back to the player's best row).
/// Then it returns a small window of rows centred on the player for context.
/// </summary>
internal sealed class LeaderboardRanker
{
    private int? _sessionBest;
    private string _sessionKey = "";

    /// <summary>Reset session-scoped state (e.g. session best) when the driver changes.</summary>
    private void MaybeRollSession(string driver)
    {
        if (driver != _sessionKey)
        {
            _sessionKey = driver;
            _sessionBest = null;
        }
    }

    public LbResult Compute(AccSnapshot snap, IReadOnlyList<LbBaseline> baseline,
                            string me, int topN, int around)
    {
        MaybeRollSession(me);

        // Track session best (ACC's iBestTime is session-scoped).
        if (IsRealTime(snap.IBestTime) && (_sessionBest == null || snap.IBestTime < _sessionBest))
            _sessionBest = snap.IBestTime;

        // Build the baseline rows; fold the player's session best into their row.
        var rows = baseline
            .Select(b => new LbRow { Name = b.Name, Ms = b.Ms, IsMe = NameEq(b.Name, me) })
            .ToList();

        if (_sessionBest is int sb)
        {
            var mine = rows.FirstOrDefault(r => NameEq(r.Name, me));
            if (mine != null) { if (sb < mine.Ms) mine.Ms = sb; }
            else rows.Add(new LbRow { Name = me, Ms = sb, IsMe = true });
        }

        // Live projected row: only while the lap is valid and a projection exists.
        LbRow? live = null;
        if (snap.LapValid && IsRealTime(snap.IEstimatedLapTime))
            live = new LbRow { Name = me, Ms = snap.IEstimatedLapTime, IsMe = true, IsLive = true };

        var all = new List<LbRow>(rows);
        if (live != null) all.Add(live);
        all.Sort((a, b) => a.Ms.CompareTo(b.Ms));
        for (int i = 0; i < all.Count; i++) all[i].Position = i + 1;

        // Focus row = the live row if present, else the player's best row.
        int focus = live != null
            ? all.IndexOf(live)
            : all.FindIndex(r => r.IsMe);

        var result = new LbResult
        {
            PredictedPosition = focus >= 0 ? focus + 1 : 0,
            PredictedMs = live?.Ms ?? (focus >= 0 ? all[focus].Ms : 0),
            Window = Slice(all, focus, topN),
        };
        return result;
    }

    // A window of up to topN rows centred on `focus`, clamped to the list ends.
    private static List<LbRow> Slice(List<LbRow> all, int focus, int topN)
    {
        if (all.Count == 0) return new();
        topN = Math.Clamp(topN, 1, all.Count);
        if (focus < 0) return all.Take(topN).ToList();

        int half = (topN - 1) / 2;
        int start = Math.Clamp(focus - half, 0, all.Count - topN);
        return all.GetRange(start, topN);
    }

    private static bool IsRealTime(int ms) => ms > 0 && ms < AccConst.IntMax;

    private static bool NameEq(string a, string b) =>
        string.Equals(a?.Trim(), b?.Trim(), StringComparison.OrdinalIgnoreCase);
}
