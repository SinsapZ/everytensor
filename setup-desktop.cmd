@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple -r requirements.txt
if errorlevel 1 (
  echo Desktop setup failed. Check the connection and Python environment.
  pause
  exit /b 1
)
echo Desktop ready. Open launch.cmd to start EveryTensor.
pause
