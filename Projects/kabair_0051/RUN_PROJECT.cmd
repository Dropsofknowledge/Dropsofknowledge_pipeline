@echo off
REM Auto-generated project launcher. Passes the project root explicitly.
setlocal
set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "DOK_ROOT=C:\Users\Mahmud\Documents\ChatGPT\drops\repo"
powershell -NoProfile -ExecutionPolicy Bypass -File "%DOK_ROOT%\scripts\render_project.ps1" -ProjectRoot "%PROJECT_ROOT%" -RootDir "%DOK_ROOT%"
echo.
pause
endlocal
