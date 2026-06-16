# Test pull from the Quix Data Lake query API, using the dashboard's own cred
# resolution + SQL. Loads creds from a .env so they don't need re-exporting.
# PowerShell 5.1 compatible.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $scriptDir

# Load .env files (first value per key wins; real env vars take precedence).
$envFiles = @(
    (Join-Path $repo "telemetry-dashboard\.env"),
    (Join-Path $repo ".env"),
    (Join-Path $scriptDir ".env")
)
foreach ($f in $envFiles) {
    if (Test-Path $f) {
        Write-Host "Loading $f"
        Get-Content $f | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
                $idx = $line.IndexOf("=")
                $k = $line.Substring(0, $idx).Trim()
                $v = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
                if ($k -and -not (Test-Path "env:$k")) { Set-Item -Path "env:$k" -Value $v }
            }
        }
    }
}

# Resolve a venv (repo .venv, else script-local), install httpx, run the probe.
$venv = Join-Path $repo ".venv"
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) { $venv = Join-Path $scriptDir ".venv" }
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) { python -m venv $venv; $py = Join-Path $venv "Scripts\python.exe" }

& $py -m pip install --quiet --disable-pip-version-check httpx
& $py (Join-Path $scriptDir "test_datalake.py")
