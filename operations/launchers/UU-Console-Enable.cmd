@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\Set-UUConsoleMode.ps1" -Action Enable
pause
