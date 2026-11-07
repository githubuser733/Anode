@echo off
"C:\Users\Zhang\AppData\Local\Programs\Python\Python312\python.exe" -X utf8 -B "%~dp0..\..\scripts\wechat_monitor\pipeline.py" %*
exit /b %ERRORLEVEL%
