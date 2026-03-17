@echo off
REM CorpPilot Startup Script (Windows)
REM Multi-Agent Collaboration System

echo.
echo ========================================
echo   CorpPilot - Enterprise Brain
echo   Multi-Agent Collaboration System
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found, please install Python 3.10+
    pause
    exit /b 1
)

REM Change to project directory
cd /d "%~dp0"

REM Initialize sample data (if not exists)
if not exist "data\tasks.json" (
    echo [INIT] Generating sample data...
    python scripts\init_sample_data.py --tasks 8
    echo.
)

REM Sync Agent config
echo [CONFIG] Syncing Agent config...
python scripts\sync_agent_config.py sync >nul 2>&1

REM Start server
echo [START] Starting Dashboard server...
echo.
echo Address: http://localhost:7891
echo Dashboard: http://localhost:7891/dashboard
echo.
echo Press Ctrl+C to stop
echo.

python dashboard\server.py --host 0.0.0.0 --port 7891

pause
