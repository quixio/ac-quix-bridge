# Launch the AC video streaming recorder with a selectable env profile.
# Usage: .\start-video.ps1 [-EnvProfile byox|quixdev]   (default: quixdev)
param([ValidateSet("byox", "quixdev")][string]$EnvProfile = "quixdev")

$envFile = "C:\repos\ac-quix-bridge\env\.env.$EnvProfile"
if (-not (Test-Path $envFile)) {
    Write-Host "Env file not found: $envFile" -ForegroundColor Red
    exit 1
}
$env:ENV_FILE = $envFile
& C:\repos\ac-quix-bridge\.venv\Scripts\python.exe C:\repos\ac-quix-bridge\ac_video_streaming\main.py
