@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
set result=%errorlevel%
if not %result%==0 pause
exit /b %result%
