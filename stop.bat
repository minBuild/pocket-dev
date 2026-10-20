@echo off
rem Pocket-Dev - stop the background server started by start_hidden.bat or the PocketDev task
cd /d "%~dp0"
".venv\Scripts\python.exe" "%~dp0server\supervisor.py" stop
pause
