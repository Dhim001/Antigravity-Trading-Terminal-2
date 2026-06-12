@echo off
echo Starting Bot Worker (requires REDIS_URL + TERMINAL_ROLE=worker)...
set TERMINAL_ROLE=worker
if "%REDIS_URL%"=="" set REDIS_URL=redis://127.0.0.1:6379/0
python worker.py
pause
