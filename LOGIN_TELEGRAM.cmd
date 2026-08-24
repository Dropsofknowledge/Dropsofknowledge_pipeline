@echo off
REM ===============================================================
REM  One-time Telegram login for DropsofKnowledge.
REM  Creates the session file so telegram_fetch.py never needs
REM  manual interaction again. Requires TELEGRAM_API_ID and
REM  TELEGRAM_API_HASH in .env first.
REM ===============================================================
setlocal
set "DOK_ROOT=%~dp0"
if "%DOK_ROOT:~-1%"=="\" set "DOK_ROOT=%DOK_ROOT:~0,-1%"
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

echo DropsofKnowledge - Telegram one-time login
echo ==========================================
echo.
"%PYEXE%" "%DOK_ROOT%\scripts\telegram_login.py"
echo.
pause
endlocal
