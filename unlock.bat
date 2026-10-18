@echo off
rem Pocket-Dev - unlock the public URL after too many failed logins
cd /d "%~dp0"
".venv\Scripts\python.exe" -m server.auth_setup unlock
pause
