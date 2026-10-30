@echo off
rem Jarvis Chrome launcher (see Jarvis-Chrome.ps1). First time: Jarvis-Chrome.cmd -Setup
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Jarvis-Chrome.ps1" %*
if errorlevel 1 pause
