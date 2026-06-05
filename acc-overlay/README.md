# ACC Overlay

A transparent, **click-through**, always-on-top overlay for **Assetto Corsa
Competizione**. It sits over the running game, ignores all mouse/keyboard input
(so it never gets in your way), and shows two things, centred at the top of the
screen:

- **G-force meter** — a friction-circle "g-ball" (lateral × longitudinal), with
  LAT / G / LON readouts.
- **Predicted leaderboard position** — your live projected lap time slotted into
  the leaderboard, with a big `P#` badge and a small window of rivals around you.

It reads ACC's shared memory directly — no Kafka/Quix runtime required — and is
designed for a single large display (built/tested against **5120 × 1440**).

> **Not for the original Assetto Corsa.** ACC uses the same shared-memory names
> but a different, richer struct layout. The struct definitions here
> ([`SharedMemory/AccStructs.cs`](SharedMemory/AccStructs.cs)) match **ACC**.
> The original-AC layout lives in [`../ac-telemetry-source/models.py`](../ac-telemetry-source/models.py).

## Requirements

- Windows + **.NET 8 Desktop Runtime** (to run) — already present on the dev box.
- **.NET 8 SDK** (to build): `winget install Microsoft.DotNet.SDK.8`
- ACC must run in **Windowed** or **Borderless** mode. A topmost overlay cannot
  draw over **exclusive fullscreen** — set ACC to *Borderless* in its video
  settings (Windows menu → settings, or `Fullscreen = 0` in `methodLight.json`).

## Build & run

```powershell
cd acc-overlay
dotnet run -c Release
```

Or produce a single self-contained EXE (no runtime needed on the target):

```powershell
dotnet publish -c Release -r win-x64 --self-contained -p:PublishSingleFile=true
# -> bin/Release/net8.0-windows/win-x64/publish/AccOverlay.exe
```

To close the overlay (it has no UI to click — it's click-through): end the
`AccOverlay` process via Task Manager, or run it from a console and Ctrl-C.

> **Smart App Control / WDAC:** on a locked-down machine the unsigned
> `AccOverlay.exe` apphost may be blocked ("An Application Control policy has
> blocked this file"). Launch it through the trusted runtime host instead:
> `dotnet "bin\Release\net8.0-windows\AccOverlay.dll"` (or just `dotnet run`).
> A code-signed published EXE is not blocked.

## Configuration — [`appsettings.json`](appsettings.json)

Copied next to the EXE on build; edit and relaunch.

| Key | Default | Meaning |
|---|---|---|
| `DashboardUrl` | `""` | Base URL of the `telemetry-dashboard`. Its `/leaderboard` proxy (all-time fastest lap per driver, from the Data Lake) is polled. **Empty = offline placeholder board.** |
| `DriverName` | `""` | Overrides the leaderboard "me" match. Empty = use ACC's player name from shared memory. |
| `LeaderboardPollSeconds` | `15` | How often to refresh the baseline board. |
| `TopN` | `5` | Rows shown in the mini-board (windowed around you). |
| `RefreshHz` | `30` | Telemetry read / G-meter refresh rate. |
| `GLateralIndex` / `GLongitudinalIndex` | `0` / `2` | Which `accG[]` components map to lateral / longitudinal. |
| `InvertLateral` / `InvertLongitudinal` | `false` | Flip a G axis if the dot moves the wrong way. |
| `TopMarginDip` | `28` | Gap from the top of the screen to the cluster. |
| `Scale` | `1.0` | Uniform size multiplier for the whole cluster. |
| `MonitorOverride` | `null` | Explicit window bounds in DIPs `{Left,Top,Width,Height}` for multi-monitor; omit to use the primary screen. |

### Leaderboard logic

Mirrors the [`scichart.html`](../telemetry-dashboard/static/scichart.html)
dashboard:

1. Baseline = all-time best lap per driver (from `/leaderboard`, or the built-in
   placeholder when no URL is set / it's unreachable).
2. Your session best (`iBestTime`) folds into your baseline row when faster.
3. A **live** row = your projected lap (`iEstimatedLapTime` = best + live delta)
   is inserted while the current lap is **valid**; it re-sorts as the lap evolves
   — climbing on a hot lap, sliding back on a slow one.
4. `P#` = the rank of that live row.

## How the click-through works

On window init the overlay sets the extended window styles
`WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`
([`MainWindow.xaml.cs`](MainWindow.xaml.cs)), so every click passes through to ACC,
it never steals focus, and it stays out of Alt-Tab.

## Layout of this project

| Path | Role |
|---|---|
| [`SharedMemory/AccStructs.cs`](SharedMemory/AccStructs.cs) | ACC physics/graphics/static struct prefixes. |
| [`SharedMemory/AccTelemetry.cs`](SharedMemory/AccTelemetry.cs) | Opens the mapped files, projects a snapshot. |
| [`Services/LeaderboardClient.cs`](Services/LeaderboardClient.cs) | Polls the dashboard `/leaderboard` proxy. |
| [`Services/LeaderboardRanker.cs`](Services/LeaderboardRanker.cs) | Predicted-position ranking logic. |
| [`Config/OverlayConfig.cs`](Config/OverlayConfig.cs) | `appsettings.json` loader. |
| [`MainWindow.xaml`](MainWindow.xaml) / [`.cs`](MainWindow.xaml.cs) | The transparent overlay window + render loop. |
