@echo off
rem Pocket-Dev server - double-click to start. Closing this window stops the server.
rem Restart from the web UI (exit code 3) starts the server again in this window.
cd /d "%~dp0"
set POCKETDEV_SUPERVISED=1
:run
".venv\Scripts\python.exe" -m server.main
if %errorlevel%==3 (
  echo.
  echo   Restarting Pocket-Dev...
  goto run
)
pause
