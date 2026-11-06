@echo off
setlocal
if /I "%~1"=="-CheckOnly" goto CheckOnly
set "GHOST_FOCUS_POLICY=background"
set "GHOST_FOCUS_LOCK=on"
"%~dp0..\..\..\ghost\local\runtime\ghost-mcp.exe"
exit /b %ERRORLEVEL%
:CheckOnly
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\Test-GhostProject.ps1" -SkipDoctor
exit /b %ERRORLEVEL%
