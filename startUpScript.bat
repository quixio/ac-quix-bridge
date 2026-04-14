@echo off
powershell -Command "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force"
echo PowerShell script execution enabled.
powershell -NoExit -ExecutionPolicy RemoteSigned -File "%~dp0start-local.ps1"
