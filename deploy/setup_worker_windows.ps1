# Bulart Worker — setup script for Windows Desktop
# Run as: powershell -ExecutionPolicy Bypass -File setup_worker_windows.ps1

$ErrorActionPreference = "Stop"
$BULART = "C:\Games\bulart"
$VENV = "$BULART\.venv_worker"

Write-Host "=== Bulart Worker Setup ===" -ForegroundColor Cyan

# 1. venv
Write-Host "`n[1/4] Creating virtual environment..." -ForegroundColor Yellow
python -m venv $VENV

# 2. deps
Write-Host "`n[2/4] Installing dependencies..." -ForegroundColor Yellow
& "$VENV\Scripts\pip" install --upgrade pip -q
& "$VENV\Scripts\pip" install -r "$BULART\requirements_worker.txt"

# 3. check Ollama
Write-Host "`n[3/4] Checking Ollama..." -ForegroundColor Yellow
try {
    $models = Invoke-RestMethod -Uri "http://localhost:11434/api/tags" -Method Get
    Write-Host "Ollama OK. Models:" -ForegroundColor Green
    $models.models | ForEach-Object { Write-Host "  - $($_.name)" }
} catch {
    Write-Host "WARNING: Ollama not reachable at localhost:11434. Start it before running worker." -ForegroundColor Red
}

# 4. create start script
Write-Host "`n[4/4] Creating start_worker.bat..." -ForegroundColor Yellow
$bat = @"
@echo off
cd /d C:\Games\bulart
set WORKER_CONFIG=config/worker_desktop.yaml
.venv_worker\Scripts\python -m worker.main --config %WORKER_CONFIG%
pause
"@
$bat | Out-File -FilePath "$BULART\start_worker.bat" -Encoding ascii

Write-Host "`n=== Done ===" -ForegroundColor Green
Write-Host "Run the worker: start_worker.bat" -ForegroundColor Cyan
Write-Host "Or add it to Windows startup via Task Scheduler." -ForegroundColor Cyan
