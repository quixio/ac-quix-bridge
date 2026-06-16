# Preview the telemetry dashboard with synthetic telemetry (no broker / AC / Quix needed).
# Sets up a script-local venv, installs FastAPI + uvicorn, then serves the real
# static/index.html with a fake telemetry feed so you can eyeball the F1 layout.
#
# PowerShell 5.1 compatible.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Resolve a venv: repo-root .venv if it exists, else script-local scripts/.venv.
$repoVenv = Join-Path (Split-Path -Parent $scriptDir) ".venv"
if (Test-Path (Join-Path $repoVenv "Scripts\python.exe")) {
    $venv = $repoVenv
} else {
    $venv = Join-Path $scriptDir ".venv"
}
$py = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $py)) {
    Write-Host "Creating venv at $venv ..."
    python -m venv $venv
}

Write-Host "Installing dependencies (fastapi, uvicorn) ..."
& $py -m pip install --quiet --disable-pip-version-check fastapi "uvicorn[standard]"

Write-Host ""
Write-Host "Starting preview server — open http://localhost:8000 (fullscreen for TV)"
Write-Host ""
& $py (Join-Path $scriptDir "preview_dashboard.py")
