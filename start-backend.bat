@echo off
REM One-click Alpha Terminal backend. Keep this window open while you work —
REM a server you start yourself is never killed by background-session cleanup.
REM --reload picks up code changes automatically.
set "PATH=%PATH%;%APPDATA%\Python\Scripts"
cd /d "%~dp0"
echo Starting Alpha Terminal backend on http://127.0.0.1:8000 ...
poetry run uvicorn app.backend.main:app --host 127.0.0.1 --port 8000 --reload
pause
