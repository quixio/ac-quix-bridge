param(
  [int]$Port = 8000
)

# ---------------------------------------------------------------------------
# Launch the Telemetry Dashboard locally to preview the SciChart.js page at
#   http://localhost:<port>/scichart.html
#
# Installs only the HTTP-serving deps (fastapi, uvicorn, httpx) into a venv —
# NOT quixstreams — so the server starts quickly. Without a live Quix feed the
# SciChart page runs in DEMO mode (synthetic telemetry), which is all you need
# to evaluate the library. Live mode needs the deployed service + Quix creds.
#
# Note: with quixstreams absent you'll see periodic "Kafka consumer failed"
# log lines — that's the consumer thread retrying and is harmless here; the
# page and its /ws endpoint serve fine regardless.
# ---------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot  = Split-Path -Parent $scriptDir
$service   = Join-Path $repoRoot 'telemetry-dashboard'

# --- Resolve a venv: reuse an existing one if present, else create script-local ---
$venv = $null
$candidates = @(
  (Join-Path $service '.venv'),
  (Join-Path $repoRoot '.venv'),
  (Join-Path $scriptDir '.venv')
)
foreach ($c in $candidates) {
  if (Test-Path (Join-Path $c 'Scripts\python.exe')) { $venv = $c; break }
}
if ($null -eq $venv) {
  $venv = Join-Path $scriptDir '.venv'
  Write-Host "Creating venv at $venv ..."
  python -m venv $venv
}
$py = Join-Path $venv 'Scripts\python.exe'

# --- Install minimal serving deps ---
Write-Host "Installing deps (fastapi, uvicorn[standard], httpx) ..."
& $py -m pip install --quiet --disable-pip-version-check fastapi "uvicorn[standard]" httpx

# --- Load telemetry-dashboard/.env if present (only sets vars not already set) ---
$envFile = Join-Path $service '.env'
if (Test-Path $envFile) {
  Write-Host "Loading .env ..."
  Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
      $idx = $line.IndexOf('=')
      $k = $line.Substring(0, $idx).Trim()
      $v = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
      if (-not [Environment]::GetEnvironmentVariable($k)) {
        [Environment]::SetEnvironmentVariable($k, $v)
      }
    }
  }
}

Write-Host ""
Write-Host "======================================================"
Write-Host "  Open once Uvicorn reports 'Application startup complete':"
Write-Host "    SciChart preview : http://localhost:$Port/scichart.html"
Write-Host "    Live dashboard   : http://localhost:$Port/"
Write-Host "======================================================"
Write-Host ""

Push-Location $service
try {
  & $py -m uvicorn main:api --host 127.0.0.1 --port $Port
} finally {
  Pop-Location
}
