@echo off
rem Pocket-Dev server - double-click to start. Closing this window stops the server.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m server.main
pause
