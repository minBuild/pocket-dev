@echo off
rem Pocket-Dev login setup - sets ID/password and issues a new access token (.env)
cd /d "%~dp0"
".venv\Scripts\python.exe" -m server.auth_setup setup
pause
