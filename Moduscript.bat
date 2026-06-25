@echo off
setlocal EnableDelayedExpansion
title Moduscript - Server + Deploy Receiver
cd /d "%~dp0"

set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo [ERROR] Virtual environment not found: %PY%
    echo Run scripts\setup.ps1 first.
    pause
    exit /b 1
)

set MCMOD_DEPLOY_INTEGRATED=1
set PYTHONUNBUFFERED=1

echo ========================================
echo  Moduscript - Web Server + Deploy Receiver
echo  Web:    http://127.0.0.1:8000
echo  Deploy: receiver 127.0.0.1:9001
echo  Local dev only: use scripts\run.ps1
echo  Ctrl+C stops current server cycle
echo ========================================
echo.

REM Deploy receiver (same console, background)
start /b "" "%PY%" -u deploy\receiver.py
if errorlevel 1 (
    echo [ERROR] Failed to start deploy receiver
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
echo [deploy] receiver started (integrated mode)
echo.

:server_loop
echo [%date% %time%] Starting server...
cd /d "%CD%\server"
"%PY%" -u main.py
set "RC=!ERRORLEVEL!"
cd /d "%~dp0"
echo.
echo [MCmodAgent] Server stopped (exit !RC!). Restarting in 2s...
timeout /t 2 /nobreak >nul
goto server_loop
