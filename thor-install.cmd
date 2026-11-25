@echo off
powershell.exe -NoProfile -File "%~dp0thor-install.ps1" %*
exit /b %errorlevel%
