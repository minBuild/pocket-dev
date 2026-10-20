@echo off
rem Pocket-Dev - start in the background (no window). Logs: data\server.log  Stop: stop.bat
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" "%~dp0server\supervisor.py"
echo Pocket-Dev started in the background. Logs: data\server.log   Stop: stop.bat
timeout /t 3 >nul
