@echo off
setlocal
set "PSModulePath=%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules;%ProgramFiles%\WindowsPowerShell\Modules"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\scripts\Set-UUConsoleMode.ps1" -Action Restore
set "UU_RESULT=%ERRORLEVEL%"
if not "%~1"=="--no-pause" pause
exit /b %UU_RESULT%
