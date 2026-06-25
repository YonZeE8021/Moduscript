@echo off
setlocal
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=py -3"
)

if "%PY%"=="py -3" (
    py -3 "%~dp0cli.py" deploy %*
) else (
    "%PY%" "%~dp0cli.py" deploy %*
)
if errorlevel 1 pause