@echo off
powershell -Command "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force"
powershell -NoExit -ExecutionPolicy RemoteSigned -File "%~dp0run.ps1"
