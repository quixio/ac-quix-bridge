# Start AC Telemetry Source and AC Video Streaming locally
# Both run in parallel in separate PowerShell windows
# Uses a single shared .venv in the repo root

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$sourceDir = Join-Path $root "ac-telemetry-source"
$videoDir = Join-Path $root "ac_video_streaming"

# --- Shared venv setup ---
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    Write-Host "Creating shared .venv..." -ForegroundColor Yellow
    py -3.12 -m venv $venv
}

# Install deps from both requirements.txt if needed
$installed = & "$venv\Scripts\pip.exe" freeze 2>$null
$needsInstall = $false
foreach ($reqFile in @("$sourceDir\requirements.txt", "$videoDir\requirements.txt")) {
    $required = Get-Content $reqFile | Where-Object { $_ -match '^\w' }
    if ($required | Where-Object { $pkg = ($_ -split '[=<>!]')[0]; -not ($installed -match "^$pkg==") }) {
        $needsInstall = $true
        break
    }
}
if ($needsInstall) {
    Write-Host "Installing dependencies..." -ForegroundColor Yellow
    & "$venv\Scripts\pip.exe" install -r "$sourceDir\requirements.txt" -r "$videoDir\requirements.txt"
} else {
    Write-Host "Dependencies up to date." -ForegroundColor Gray
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

# Start each in a new PowerShell window using the shared venv
# IMPORTANT: telemetry must start FIRST so that its session_id is published
# to `ac-telemetry-session` BEFORE the video source starts polling that topic.
# The video source adopts telemetry's session_id so the MP4 lap files end up
# in the same S3 folder the Telemetry Explorer expects.
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$sourceDir'
    & '$venv\Scripts\Activate.ps1'
    Write-Host 'Starting AC Telemetry Source...' -ForegroundColor Green
    python main.py
" -WorkingDirectory $sourceDir

# Give telemetry source time to:
#   - initialize QuixStreams (portal API call, ~1-2s)
#   - connect to AC shared memory and detect off->live
#   - publish the session_id to ac-telemetry-session
# Video then starts with the message already in the (compacted) topic.
$telemetryHeadStartSeconds = 7
Write-Host ""
Write-Host "Waiting $telemetryHeadStartSeconds seconds for telemetry source to publish session_id..." -ForegroundColor Yellow
for ($i = $telemetryHeadStartSeconds; $i -gt 0; $i--) {
    Write-Host -NoNewline "`r  $i ... "
    Start-Sleep -Seconds 1
}
Write-Host "`r  done.        " -ForegroundColor Yellow
Write-Host ""

Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$videoDir'
    & '$venv\Scripts\Activate.ps1'
    `$env:PATH += ';$ffmpegDir'
    Write-Host 'Starting AC Video Streaming...' -ForegroundColor Green
    python main.py
" -WorkingDirectory $videoDir

Write-Host "Both services launched in separate windows." -ForegroundColor Green
