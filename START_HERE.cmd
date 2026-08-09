@echo off
REM ===============================================================
REM  DropsofKnowledge Renderer - root entry point (spec 8)
REM  Passes the Dok root explicitly; never relies on the CWD.
REM ===============================================================
setlocal
set "DOK_ROOT=%~dp0"
if "%DOK_ROOT:~-1%"=="\" set "DOK_ROOT=%DOK_ROOT:~0,-1%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%DOK_ROOT%\scripts\start_here.ps1" -RootDir "%DOK_ROOT%"
endlocal
