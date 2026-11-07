@echo off
if "%~1"=="" goto status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\scripts\Control-WeChatResident.ps1" -Action %*
exit /b %ERRORLEVEL%
:status
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\..\scripts\Control-WeChatResident.ps1" -Action Status
exit /b %ERRORLEVEL%
