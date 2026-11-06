@echo off
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\Test-GhostProject.ps1"
set "GHOST_CHECK_EXIT=%ERRORLEVEL%"
if /I "%~1"=="-NoPause" exit /b %GHOST_CHECK_EXIT%
pause
exit /b %GHOST_CHECK_EXIT%
