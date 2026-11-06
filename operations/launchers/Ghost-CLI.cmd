@echo off
setlocal
set "GHOST_FOCUS_POLICY=background"
set "GHOST_FOCUS_LOCK=on"
"%~dp0..\..\..\ghost\local\runtime\ghost.exe" %*
exit /b %ERRORLEVEL%
