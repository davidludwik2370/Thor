@echo off
setlocal
rem Windows clients default to Restricted when no policy has been configured.
rem Use RemoteSigned for this process only in that case. Preserve every explicit
rem policy, including inherited process settings and organization Group Policy.
for /f "delims=" %%P in ('powershell.exe -NoProfile -Command "if (@((Get-ExecutionPolicy -List).ExecutionPolicy -ne 'Undefined').Count -eq 0) { 'RemoteSigned' }"') do if "%%P"=="RemoteSigned" set "PSExecutionPolicyPreference=RemoteSigned"
powershell.exe -NoProfile -File "%~dp0thor-install.ps1" %*
exit /b %errorlevel%
