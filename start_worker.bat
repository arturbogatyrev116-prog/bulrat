@echo off
cd /d C:\Games\bulart
set WORKER_CONFIG=config/worker_desktop.yaml
.venv_worker\Scripts\python -m worker.main --config %WORKER_CONFIG%
pause
