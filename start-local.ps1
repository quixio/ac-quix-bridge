# Start AC Telemetry Source and AC Video Streaming locally
# Both run in parallel in separate PowerShell windows

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# --- AC Telemetry Source ---
$sourceDir = Join-Path $root "ac-telemetry-source"
$sourceVenv = Join-Path $sourceDir ".venv"
$sourcePython = Join-Path $sourceVenv "Scripts\python.exe"
if (-not (Test-Path $sourcePython)) {
    Write-Host "Creating venv for ac-telemetry-source..." -ForegroundColor Yellow
    py -3.12 -m venv $sourceVenv
}
$sourceInstalled = & "$sourceVenv\Scripts\pip.exe" freeze 2>$null
$sourceRequired = Get-Content "$sourceDir\requirements.txt" | Where-Object { $_ -match '^\w' }
if ($sourceRequired | Where-Object { $pkg = ($_ -split '[=<>!]')[0]; -not ($sourceInstalled -match "^$pkg==") }) {
    Write-Host "Installing dependencies for ac-telemetry-source..." -ForegroundColor Yellow
    & "$sourceVenv\Scripts\pip.exe" install -r "$sourceDir\requirements.txt"
} else {
    Write-Host "ac-telemetry-source dependencies up to date." -ForegroundColor Gray
}

# --- AC Video Streaming ---
$videoDir = Join-Path $root "ac_video_streaming"
$videoVenv = Join-Path $videoDir ".venv"
$videoPython = Join-Path $videoVenv "Scripts\python.exe"
if (-not (Test-Path $videoPython)) {
    Write-Host "Creating venv for ac_video_streaming..." -ForegroundColor Yellow
    py -3.12 -m venv $videoVenv
}
$videoInstalled = & "$videoVenv\Scripts\pip.exe" freeze 2>$null
$videoRequired = Get-Content "$videoDir\requirements.txt" | Where-Object { $_ -match '^\w' }
if ($videoRequired | Where-Object { $pkg = ($_ -split '[=<>!]')[0]; -not ($videoInstalled -match "^$pkg==") }) {
    Write-Host "Installing dependencies for ac_video_streaming..." -ForegroundColor Yellow
    & "$videoVenv\Scripts\pip.exe" install -r "$videoDir\requirements.txt"
} else {
    Write-Host "ac_video_streaming dependencies up to date." -ForegroundColor Gray
}

# --- Add ffmpeg to PATH if not already available ---
$ffmpegDir = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
if ((Test-Path $ffmpegDir) -and -not ($env:PATH -like "*$ffmpegDir*")) {
    $env:PATH += ";$ffmpegDir"
}

Write-Host ""
Write-Host "Starting services..." -ForegroundColor Green
Write-Host "  AC Telemetry Source  (ac-telemetry-source)" -ForegroundColor Cyan
Write-Host "  AC Video Streaming   (ac_video_streaming)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C in each window to stop." -ForegroundColor Gray
Write-Host ""

# Start each in a new PowerShell window
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$sourceDir'
    & '$sourceVenv\Scripts\Activate.ps1'
    Write-Host 'Starting AC Telemetry Source...' -ForegroundColor Green
    python main.py
" -WorkingDirectory $sourceDir

Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$videoDir'
    & '$videoVenv\Scripts\Activate.ps1'
    `$env:PATH += ';$ffmpegDir'
    Write-Host 'Starting AC Video Streaming...' -ForegroundColor Green
    python main.py
" -WorkingDirectory $videoDir

Write-Host "Both services launched in separate windows." -ForegroundColor Green
