
@echo off
title Medicine Buddy - Setup
cd /d "%~dp0"

echo ==================================
echo       MEDICINE BUDDY SETUP
echo ==================================

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Removing old PostgreSQL drivers...
python -m pip uninstall -y psycopg2 psycopg2-binary psycopg

echo Installing project dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if errorlevel 1 (
    echo.
    echo SETUP FAILED. Please share the error above.
    pause
    exit /b 1
)

echo.
echo SETUP COMPLETED SUCCESSFULLY!
pause
