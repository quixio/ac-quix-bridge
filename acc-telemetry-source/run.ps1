# Run ACC Telemetry Source standalone (service-local .venv)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$venv = Join-Path $root ".venv"
$py   = Join-Path $venv "Scripts\python.exe"
$pip  = Join-Path $venv "Scripts\pip.exe"
$req  = Join-Path $root "requirements.txt"

if (-not (Test-Path $py)) {
    Write-Host "Creating .venv..." -ForegroundColor Yellow
    py -3.12 -m venv $venv
}

$installed = & $pip freeze 2>$null
$required  = Get-Content $req | Where-Object { $_ -match '^\w' }
$missing   = $required | Where-Object { $pkg = ($_ -split '[=<>!]')[0]; -not ($installed -match "^$pkg==") }
if ($missing) {
    Write-Host "Installing deps..." -ForegroundColor Yellow
    & $pip install -r $req
} else {
    Write-Host "Deps up to date." -ForegroundColor Gray
}

& "$venv\Scripts\Activate.ps1"
Set-Location $root
Write-Host "Starting ACC Telemetry Source..." -ForegroundColor Green
python main.py
