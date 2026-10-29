# Jarvis installer for Windows (PowerShell). Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip wheel
.\.venv\Scripts\pip install -e ".[voice]" pycaw comtypes plyer
.\.venv\Scripts\python -m playwright install chromium
.\.venv\Scripts\jarvis init
Write-Host "`nInstalled. Add your key to $env:USERPROFILE\.jarvis\.env, then run:"
Write-Host "  .\.venv\Scripts\jarvis doctor"
Write-Host "  .\.venv\Scripts\jarvis voice"
