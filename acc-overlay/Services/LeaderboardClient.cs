using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AccOverlay.Services;

/// <summary>A settled all-time best lap for one driver (the leaderboard baseline).</summary>
internal sealed class LbBaseline
{
    public string Name { get; init; } = "";
    public int Ms { get; init; }
}

/// <summary>
/// Polls the telemetry-dashboard's <c>/leaderboard</c> proxy (all-time fastest
/// lap per driver, backed by the Data Lake) on a background loop. The latest
/// successful result is exposed via <see cref="Rows"/>; failures leave the last
/// good board in place (matching the dashboard's own behaviour). If no URL is
/// configured it serves a static placeholder so the overlay still ranks the
/// player sensibly offline.
/// </summary>
internal sealed class LeaderboardClient : IDisposable
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(10) };
    private readonly string? _url;
    private readonly TimeSpan _interval;
    private readonly CancellationTokenSource _cts = new();

    private volatile List<LbBaseline> _rows;

    public LeaderboardClient(string? dashboardUrl, int pollSeconds)
    {
        _url = string.IsNullOrWhiteSpace(dashboardUrl)
            ? null
            : dashboardUrl.TrimEnd('/') + "/leaderboard";
        _interval = TimeSpan.FromSeconds(Math.Max(5, pollSeconds));
        _rows = Placeholder();
    }

    /// <summary>Most recent leaderboard baseline. Always non-null.</summary>
    public List<LbBaseline> Rows => _rows;

    public void Start() => _ = Task.Run(() => LoopAsync(_cts.Token));

    private async Task LoopAsync(CancellationToken ct)
    {
        if (_url == null)
            return; // no endpoint configured -> keep the placeholder board

        // Poll immediately, then on the interval.
        while (!ct.IsCancellationRequested)
        {
            await FetchOnceAsync(ct).ConfigureAwait(false);
            try { await Task.Delay(_interval, ct).ConfigureAwait(false); }
            catch (TaskCanceledException) { break; }
        }
    }

    private async Task FetchOnceAsync(CancellationToken ct)
    {
        try
        {
            using var res = await _http.GetAsync(_url, ct).ConfigureAwait(false);
            if (!res.IsSuccessStatusCode)
                return;

            var payload = await res.Content
                .ReadFromJsonSafeAsync(ct)
                .ConfigureAwait(false);

            if (payload?.Rows is { Count: > 0 } rows)
            {
                _rows = rows
                    .Where(r => !string.IsNullOrWhiteSpace(r.Name) && r.Ms > 0)
                    .Select(r => new LbBaseline { Name = r.Name!.Trim(), Ms = r.Ms })
                    .ToList();
            }
        }
        catch
        {
            // Network blip / dashboard down -> keep the last good board.
        }
    }

    // A believable default board so an offline overlay still places the player.
    private static List<LbBaseline> Placeholder() => new()
    {
        new() { Name = "Verstappen", Ms = 82418 },
        new() { Name = "Leclerc",    Ms = 82673 },
        new() { Name = "Norris",     Ms = 82901 },
        new() { Name = "Hamilton",   Ms = 83244 },
        new() { Name = "Russell",    Ms = 83510 },
        new() { Name = "Sainz",      Ms = 83872 },
        new() { Name = "Piastri",    Ms = 84115 },
        new() { Name = "Alonso",     Ms = 84560 },
    };

    public void Dispose()
    {
        _cts.Cancel();
        _cts.Dispose();
        _http.Dispose();
    }
}

// --- JSON shapes for /leaderboard: {"rows":[{"name":"..","ms":12345}, ...]} ---

internal sealed class LbPayload
{
    [JsonPropertyName("rows")] public List<LbApiRow>? Rows { get; set; }
}

internal sealed class LbApiRow
{
    [JsonPropertyName("name")] public string? Name { get; set; }
    [JsonPropertyName("ms")] public int Ms { get; set; }
}

internal static class HttpContentJsonExtensions
{
    private static readonly JsonSerializerOptions Opts = new() { PropertyNameCaseInsensitive = true };

    public static async Task<LbPayload?> ReadFromJsonSafeAsync(this HttpContent content, CancellationToken ct)
    {
        await using var stream = await content.ReadAsStreamAsync(ct).ConfigureAwait(false);
        return await JsonSerializer.DeserializeAsync<LbPayload>(stream, Opts, ct).ConfigureAwait(false);
    }
}
