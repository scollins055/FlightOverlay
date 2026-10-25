@echo off
rem Start FlightOverlay from source (for development). Use build.ps1 to make the standalone .exe.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    echo Setting up for the first time...
    py -3 -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error
    powershell -ExecutionPolicy Bypass -File tools\get_ffmpeg.ps1 || goto :error
)
start "" .venv\Scripts\pythonw.exe -m flightoverlay %*
exit /b 0
:error
echo Setup failed.
pause
