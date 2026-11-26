@echo off
cd /d "%~dp0"
start "Jarvis backend" /min "%~dp0venv\Scripts\python.exe" -m uvicorn main:app
cd desktop
call npm start