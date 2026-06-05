@echo off
REM HN Doom-Scroll launcher. Double-click this or run from a terminal.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
echo.
echo Starting HN Doom-Scroll at http://localhost:8000
echo Press Ctrl+C to stop.
echo.
start "" "http://localhost:8000"
.venv\Scripts\python.exe app.py
