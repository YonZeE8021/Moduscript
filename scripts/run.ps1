# Start MCmodAgent API server using project virtual environment

$ErrorActionPreference = "Stop"
$RootDir = Split-Path $PSScriptRoot -Parent
$VenvPython = Join-Path $RootDir ".venv\Scripts\python.exe"
$ServerDir = Join-Path $RootDir "server"

if (-not (Test-Path $VenvPython)) {
    Write-Host "未找到虚拟环境: $VenvPython" -ForegroundColor Red
    Write-Host "请先运行: .\scripts\setup.ps1" -ForegroundColor Yellow
    exit 1
}

Set-Location $ServerDir
& $VenvPython main.py
