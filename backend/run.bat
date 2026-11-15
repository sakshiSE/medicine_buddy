
@echo off
title Medicine Buddy - Backend
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Please run setup.bat first.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo Starting Medicine Buddy API...
python -m uvicorn app.main:app --reload --port 8000

pause
