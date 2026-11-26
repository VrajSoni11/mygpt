@echo off
cd /d "%~dp0"
start "LOKI backend" /min "%~dp0venv\Scripts\python.exe" -m uvicorn main:app
cd desktop
call npm start
