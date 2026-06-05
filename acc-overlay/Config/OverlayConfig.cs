using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AccOverlay.Config;

/// <summary>
/// Overlay settings, loaded from <c>appsettings.json</c> next to the EXE. All
/// fields have sane defaults so a missing/partial file still runs.
/// </summary>
internal sealed class OverlayConfig
{
    /// <summary>Base URL of the telemetry-dashboard (its <c>/leaderboard</c> proxy is polled). Empty = offline placeholder board.</summary>
    public string DashboardUrl { get; set; } = "";

    /// <summary>Override the leaderboard "me" name. Empty = use ACC's player name from shared memory.</summary>
    public string DriverName { get; set; } = "";

    public int LeaderboardPollSeconds { get; set; } = 15;

    /// <summary>How many rows to show in the mini-leaderboard (windowed around the player).</summary>
    public int TopN { get; set; } = 5;

    /// <summary>Telemetry read rate (Hz) driving the G-meter smoothness.</summary>
    public int RefreshHz { get; set; } = 30;

    // --- G-force axis mapping (accG indices: 0=lateral, 1=vertical, 2=longitudinal in ACC) ---
    public int GLateralIndex { get; set; } = 0;
    public int GLongitudinalIndex { get; set; } = 2;
    public bool InvertLateral { get; set; }
    public bool InvertLongitudinal { get; set; }

    /// <summary>Distance from the top of the screen to the overlay cluster (DIPs).</summary>
    public double TopMarginDip { get; set; } = 28;

    /// <summary>Uniform scale for the whole overlay cluster (1.0 = design size).</summary>
    public double Scale { get; set; } = 1.0;

    /// <summary>Optional explicit window bounds in DIPs (for multi-monitor). Null = primary screen.</summary>
    public MonitorBounds? MonitorOverride { get; set; }

    public sealed class MonitorBounds
    {
        public double Left { get; set; }
        public double Top { get; set; }
        public double Width { get; set; }
        public double Height { get; set; }
    }

    private static readonly JsonSerializerOptions Opts = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    public static OverlayConfig Load()
    {
        try
        {
            string path = Path.Combine(AppContext.BaseDirectory, "appsettings.json");
            if (File.Exists(path))
            {
                var cfg = JsonSerializer.Deserialize<OverlayConfig>(File.ReadAllText(path), Opts);
                if (cfg != null) return cfg;
            }
        }
        catch
        {
            // Malformed config -> fall back to defaults rather than failing to start.
        }
        return new OverlayConfig();
    }
}
