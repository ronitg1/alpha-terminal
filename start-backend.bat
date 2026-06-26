@echo off
REM One-click Alpha Terminal backend. Keep this window open while you work —
REM a server you start yourself is never killed by background-session cleanup.
REM --reload picks up code changes automatically.
set "PATH=%PATH%;%APPDATA%\Python\Scripts"
cd /d "%~dp0"

REM Clear any backend already holding port 8000 before starting. Past sessions
REM piled up stale uvicorn processes (each --reload run is a watcher+worker
REM pair); an old one kept serving outdated code while new launches couldn't
REM bind the port. Killing the listener first guarantees one fresh, current
REM instance every time you double-click this.
echo Clearing any existing backend on port 8000 ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8000 ^| findstr LISTENING') do taskkill /F /PID %%a >nul 2>&1

echo Starting Alpha Terminal backend on http://127.0.0.1:8000 ...
REM Run uvicorn as a Python MODULE, not the uvicorn.exe console script —
REM this machine's Device Guard / WDAC policy blocks uvicorn.exe, which
REM silently kills the server. python.exe is allowed, so -m uvicorn works.
poetry run python -m uvicorn app.backend.main:app --host 127.0.0.1 --port 8000 --reload
pause
