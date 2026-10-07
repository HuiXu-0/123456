@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify-backend.ps1" %*
exit /b %ERRORLEVEL%
