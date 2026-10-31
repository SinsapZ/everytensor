@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
echo Installing the model runtime from PyPI into the project environment...
".venv\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple -r requirements-model.txt
if errorlevel 1 goto failed
echo Preparing the lightweight multilingual semantic model...
".venv\Scripts\python.exe" -X utf8 -u tools\install_quick_model.py
if errorlevel 1 goto failed
echo Downloading or resuming the complete local model...
".venv\Scripts\python.exe" -X utf8 -u tools\install_model.py
if errorlevel 1 goto failed
echo Verifying and configuring local model weights...
".venv\Scripts\python.exe" -u tools\verify_model.py --configure
if errorlevel 1 goto failed
echo Ready. Close and reopen EveryTensor to start semantic indexing.
pause
exit /b 0
:failed
echo Setup did not finish. See the error above; you can run this file again.
pause
exit /b 1
