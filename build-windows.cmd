@echo off
setlocal
cd /d "%~dp0"
if not exist ".build-venv\Scripts\python.exe" (
  py -3.11 -m venv .build-venv
  if errorlevel 1 exit /b 1
)
".build-venv\Scripts\python.exe" -m pip install --cache-dir build\pip-cache --index-url https://pypi.org/simple -r requirements-build.txt
if errorlevel 1 exit /b 1
".build-venv\Scripts\python.exe" -X utf8 tools\build_windows.py %*
exit /b %errorlevel%
