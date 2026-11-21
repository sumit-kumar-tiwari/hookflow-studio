@echo off
title HookFlow Downloader & Studio
echo ========================================================
echo        HookFlow Downloader - Starting Local Server
echo ========================================================
echo.
cd /d "%~dp0"
echo Server running at: http://localhost:8000
echo Press Ctrl+C to stop.
echo.
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
