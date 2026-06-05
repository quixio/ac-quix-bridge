using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
using AccOverlay.Config;
using AccOverlay.Services;
using AccOverlay.SharedMemory;

namespace AccOverlay;

public partial class MainWindow : Window
{
    // --- Win32 click-through plumbing ---------------------------------------
    // The extended-style value is 32-bit, so the classic GetWindowLong/SetWindowLong
    // (which resolve to the W exports on x64) are correct and avoid the *Ptr entry-
    // point ambiguity. WS_EX_* flags all fit in an int.
    private const int GWL_EXSTYLE = -20;
    private const int WS_EX_TRANSPARENT = 0x00000020; // clicks fall through to the game
    private const int WS_EX_LAYERED = 0x00080000;
    private const int WS_EX_TOOLWINDOW = 0x00000080;  // keep out of Alt-Tab
    private const int WS_EX_NOACTIVATE = 0x08000000;  // never steal focus

    [DllImport("user32.dll", SetLastError = true)]
    private static extern int GetWindowLong(IntPtr hWnd, int nIndex);

    [DllImport("user32.dll", SetLastError = true)]
    private static extern int SetWindowLong(IntPtr hWnd, int nIndex, int dwNewLong);

    // --- Palette (frozen brushes; mirrors the dashboard) --------------------
    private static readonly Brush Cyan = Frozen("#00D4FF");
    private static readonly Brush Green = Frozen("#00FF88");
    private static readonly Brush Amber = Frozen("#FFAA00");
    private static readonly Brush Red = Frozen("#FF3355");
    private static readonly Brush Dim = Frozen("#8A8AA5");
    private static readonly Brush TextBrush = Frozen("#F0F0F5");
    private static readonly Brush MeBg = Frozen("#1C1C30");

    private const int IntMax = 2147483647;

    private readonly OverlayConfig _cfg;
    private readonly AccTelemetry _telemetry;
    private readonly LeaderboardClient _leaderboard;
    private readonly LeaderboardRanker _ranker = new();
    private readonly DispatcherTimer _timer;

    private string _lastDriver = "";
    private int _ticks;
    private int _lbRenderEveryTicks;

    public MainWindow()
    {
        InitializeComponent();

        _cfg = OverlayConfig.Load();
        _telemetry = new AccTelemetry(
            _cfg.GLateralIndex, _cfg.GLongitudinalIndex,
            _cfg.InvertLateral, _cfg.InvertLongitudinal);
        _leaderboard = new LeaderboardClient(_cfg.DashboardUrl, _cfg.LeaderboardPollSeconds);

        ClusterScale.ScaleX = ClusterScale.ScaleY = Math.Clamp(_cfg.Scale, 0.4, 3.0);
        Root.Margin = new Thickness(0, _cfg.TopMarginDip, 0, 0);

        int hz = Math.Clamp(_cfg.RefreshHz, 5, 90);
        // Re-render the leaderboard ~6x/sec regardless of refresh rate so the ms
        // digits stay readable; the G-meter updates every tick.
        _lbRenderEveryTicks = Math.Max(1, hz / 6);
        _timer = new DispatcherTimer(DispatcherPriority.Render)
        {
            Interval = TimeSpan.FromMilliseconds(1000.0 / hz),
        };
        _timer.Tick += OnTick;

        Loaded += OnLoaded;
        Closed += OnClosed;
    }

    private void OnLoaded(object? sender, RoutedEventArgs e)
    {
        PositionWindow();
        _leaderboard.Start();
        _timer.Start();
    }

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);

        // Make the window click-through at the OS level so every click reaches ACC.
        var hwnd = new WindowInteropHelper(this).Handle;
        int ex = GetWindowLong(hwnd, GWL_EXSTYLE);
        ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE;
        SetWindowLong(hwnd, GWL_EXSTYLE, ex);
    }

    // Cover the chosen monitor so the centre-top cluster lands centred. Values are
    // in WPF DIPs; SystemParameters already reports the primary screen in DIPs.
    private void PositionWindow()
    {
        if (_cfg.MonitorOverride is { } m)
        {
            Left = m.Left; Top = m.Top; Width = m.Width; Height = m.Height;
        }
        else
        {
            Left = 0; Top = 0;
            Width = SystemParameters.PrimaryScreenWidth;
            Height = SystemParameters.PrimaryScreenHeight;
        }
    }

    private void OnTick(object? sender, EventArgs e)
    {
        var snap = _telemetry.Read();
        UpdateGMeter(snap);
        UpdateStatus(snap);

        if (_ticks++ % _lbRenderEveryTicks == 0)
            UpdateLeaderboard(snap);
    }

    // ------------------------------------------------------------------ G-meter
    private void UpdateGMeter(AccSnapshot snap)
    {
        const double pxPerG = 30.0;   // matches the 30/60/90 px rings (1/2/3 g)
        const double maxR = 90.0;     // clamp to the outer ring

        double lat = Clamp(snap.AccLateral, -4, 4);
        double lon = Clamp(snap.AccLongitudinal, -4, 4);

        double x = lat * pxPerG;
        double y = -lon * pxPerG;     // SVG-style: longitudinal accel pushes the dot UP
        double r = Math.Sqrt(x * x + y * y);
        if (r > maxR) { double s = maxR / r; x *= s; y *= s; }

        double mag = Math.Sqrt(lat * lat + lon * lon);
        Brush col = mag < 1 ? Cyan : mag < 2 ? Amber : Red;

        GVector.X2 = 100 + x;
        GVector.Y2 = 100 + y;
        GVector.Stroke = col;
        Canvas.SetLeft(GDot, 100 + x - 7.5);
        Canvas.SetTop(GDot, 100 + y - 7.5);
        GDot.Fill = col;

        GLat.Text = lat.ToString("0.0");
        GLon.Text = lon.ToString("0.0");
        GTot.Text = mag.ToString("0.0");
    }

    // ------------------------------------------------------------- Leaderboard
    private void UpdateLeaderboard(AccSnapshot snap)
    {
        string me = ResolveDriver(snap);
        var res = _ranker.Compute(snap, _leaderboard.Rows, me, _cfg.TopN, _cfg.TopN);

        PosBadge.Text = res.PredictedPosition > 0 ? "P" + res.PredictedPosition : "P--";
        PredTime.Text = FmtLap(res.PredictedMs);

        RenderRows(res.Window);
    }

    private void RenderRows(IReadOnlyList<LbRow> rows)
    {
        LbRows.Children.Clear();
        foreach (var r in rows)
            LbRows.Children.Add(BuildRow(r));
    }

    private static Border BuildRow(LbRow r)
    {
        var grid = new Grid { Margin = new Thickness(0, 1.5, 0, 1.5) };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(30) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var pos = new TextBlock
        {
            Text = r.Position.ToString(),
            Foreground = r.IsMe ? TextBrush : Dim,
            FontWeight = FontWeights.Bold,
            FontSize = 15,
            HorizontalAlignment = HorizontalAlignment.Center,
            VerticalAlignment = VerticalAlignment.Center,
        };
        Grid.SetColumn(pos, 0);

        var namePanel = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            VerticalAlignment = VerticalAlignment.Center,
        };
        if (r.IsLive)
        {
            namePanel.Children.Add(new Ellipse
            {
                Width = 8, Height = 8, Fill = Green,
                VerticalAlignment = VerticalAlignment.Center,
                Margin = new Thickness(0, 0, 6, 0),
            });
        }
        namePanel.Children.Add(new TextBlock
        {
            Text = string.IsNullOrWhiteSpace(r.Name) ? "—" : r.Name,
            Foreground = r.IsMe ? TextBrush : Dim,
            FontWeight = FontWeights.SemiBold,
            FontSize = 15,
            TextTrimming = TextTrimming.CharacterEllipsis,
            VerticalAlignment = VerticalAlignment.Center,
        });
        Grid.SetColumn(namePanel, 1);

        var time = new TextBlock
        {
            Text = (r.IsLive ? "~" : "") + FmtLap(r.Ms),
            Foreground = r.IsLive ? Amber : Cyan,
            FontStyle = r.IsLive ? FontStyles.Italic : FontStyles.Normal,
            FontWeight = FontWeights.Bold,
            FontSize = 15,
            HorizontalAlignment = HorizontalAlignment.Right,
            VerticalAlignment = VerticalAlignment.Center,
        };
        Grid.SetColumn(time, 2);

        grid.Children.Add(pos);
        grid.Children.Add(namePanel);
        grid.Children.Add(time);

        return new Border
        {
            Child = grid,
            Background = r.IsMe ? MeBg : Brushes.Transparent,
            CornerRadius = new CornerRadius(5),
            Padding = new Thickness(6, 2, 6, 2),
        };
    }

    // ---------------------------------------------------------------- Status
    private void UpdateStatus(AccSnapshot snap)
    {
        if (snap.Connected)
        {
            StatusDot.Fill = Green;
            string who = string.IsNullOrWhiteSpace(_lastDriver) ? "live" : _lastDriver;
            StatusText.Text = "LIVE · " + who;
            StatusText.Foreground = Dim;
        }
        else
        {
            StatusDot.Fill = Amber;
            StatusText.Text = "waiting for ACC…";
            StatusText.Foreground = Dim;
        }
    }

    // ---------------------------------------------------------------- helpers
    private string ResolveDriver(AccSnapshot snap)
    {
        if (!string.IsNullOrWhiteSpace(_cfg.DriverName))
            return _cfg.DriverName.Trim();
        if (snap.Connected && !string.IsNullOrWhiteSpace(snap.DriverName))
            _lastDriver = snap.DriverName.Trim();
        return _lastDriver;
    }

    private static string FmtLap(int ms)
    {
        if (ms <= 0 || ms >= IntMax) return "--:--.---";
        int m = ms / 60000;
        int s = ms % 60000 / 1000;
        int mm = ms % 1000;
        return $"{m}:{s:00}.{mm:000}";
    }

    private static double Clamp(double v, double lo, double hi) =>
        double.IsNaN(v) ? lo : Math.Max(lo, Math.Min(hi, v));

    private static Brush Frozen(string hex)
    {
        var b = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));
        b.Freeze();
        return b;
    }

    private void OnClosed(object? sender, EventArgs e)
    {
        _timer.Stop();
        _telemetry.Dispose();
        _leaderboard.Dispose();
    }
}
