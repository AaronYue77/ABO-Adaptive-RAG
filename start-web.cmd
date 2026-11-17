:; exec sh "$(dirname "$0")/scripts/start_web_unix.sh" "$@"
@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_web_windows.ps1" %*
set "ABO_EXIT_CODE=%ERRORLEVEL%"
if not "%ABO_EXIT_CODE%"=="0" if not defined CI pause
exit /b %ABO_EXIT_CODE%
