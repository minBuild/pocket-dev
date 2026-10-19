@echo off
rem Pocket-Dev - save the Google Stitch API key (.env)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m server.auth_setup stitch
pause
