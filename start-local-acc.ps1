# Start ACC Telemetry Source and AC Video Streaming locally
# Both run in parallel in separate PowerShell windows
# Uses a single shared .venv in the repo root

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$sourceDir = Join-Path $root "acc-telemetry-source"
$videoDir = Join-Path $root "ac_video_streaming"

# --- Target environment selector ---
Write-Host "Select target environment:" -ForegroundColor Cyan
Write-Host "  1. Byox"
Write-Host "  2. Quix Dev"
$choice = ""
while ($choice -ne "1" -and $choice -ne "2") {
    $choice = Read-Host "Enter 1 or 2"
}
if ($choice -eq "1") {
    $envName = "Byox"
    $envFile = Join-Path $root "env\.env.byox"
    $isByox = $true
} else {
    $envName = "Quix Dev"
    $envFile = Join-Path $root "env\.env.quixdev"
    $isByox = $false
}
if (-not (Test-Path $envFile)) {
    Write-Host "Env file not found: $envFile" -ForegroundColor Red
    exit 1
}

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

# --- byox self-signed TLS: feed httpx the byox CA chain via SSL_CERT_FILE ---
# Mode A's portal API call (httpx) fails verification against byox's self-signed
# chain otherwise. Auto-capture it once via fetch_byox_cert.py; child windows
# inherit SSL_CERT_FILE. Quix Dev uses public certs, so leave it unset there.
if ($isByox) {
    $certFile = Join-Path $root "certificates\byox-chain.pem"
    if (-not (Test-Path $certFile)) {
        Write-Host "Fetching byox TLS chain..." -ForegroundColor Yellow
        & "$venv\Scripts\python.exe" (Join-Path $root "fetch_byox_cert.py")
        if (-not (Test-Path $certFile)) {
            Write-Host "Failed to fetch byox cert chain ($certFile)." -ForegroundColor Red
            exit 1
        }
    }
    $env:SSL_CERT_FILE = $certFile
    Write-Host "SSL_CERT_FILE = $certFile" -ForegroundColor Gray
} else {
    Remove-Item Env:\SSL_CERT_FILE -ErrorAction SilentlyContinue
}

# --- Add ffmpeg to PATH if not already available ---
$ffmpegDir = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.1-full_build\bin"
if ((Test-Path $ffmpegDir) -and -not ($env:PATH -like "*$ffmpegDir*")) {
    $env:PATH += ";$ffmpegDir"
}

Write-Host ""
Write-Host "Starting services..." -ForegroundColor Green
Write-Host "  Target environment: $envName ($envFile)" -ForegroundColor Cyan
Write-Host "  ACC Telemetry Source (acc-telemetry-source)" -ForegroundColor Cyan
Write-Host "  AC Video Streaming   (ac_video_streaming)" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press Ctrl+C in each window to stop." -ForegroundColor Gray
Write-Host ""

# Start each in a new PowerShell window using the shared venv
# IMPORTANT: telemetry must start FIRST so that its session_id is published
# to the session topic BEFORE the video source starts polling it.
# The video source adopts telemetry's session_id so the MP4 lap files end up
# in the same S3 folder the Telemetry Explorer expects.
Start-Process powershell -ArgumentList "-NoExit", "-Command", "
    Set-Location '$sourceDir'
    & '$venv\Scripts\Activate.ps1'
    `$env:ENV_FILE = '$envFile'
    Write-Host 'Starting ACC Telemetry Source ($envName)...' -ForegroundColor Green
    python main.py
" -WorkingDirectory $sourceDir

# Give telemetry source time to:
#   - initialize QuixStreams (portal API call, ~1-2s)
#   - connect to ACC shared memory and detect off->live
#   - publish the session_id to the session topic
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
    `$env:ENV_FILE = '$envFile'
    Write-Host 'Starting AC Video Streaming ($envName)...' -ForegroundColor Green
    python main.py
" -WorkingDirectory $videoDir

Write-Host "Both services launched in separate windows." -ForegroundColor Green
