@echo off
REM ===============================================================
REM  Push a backup of all small Dropsofknowledge artifacts to the
REM  private backup repo. Raw media excluded on purpose (re-fetch
REM  from Telegram / re-render instead). Safe to run any time.
REM ===============================================================
setlocal
set "DOK_ROOT=%~dp0"
if "%DOK_ROOT:~-1%"=="\" set "DOK_ROOT=%DOK_ROOT:~0,-1%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%DOK_ROOT%\scripts\backup.ps1"
echo.
pause
endlocal
