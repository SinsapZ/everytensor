@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 -u tools\install_quick_model.py
if errorlevel 1 goto failed
echo Ready. Close and reopen EveryTensor to enable quick semantic search.
pause
exit /b 0
:failed
set "QUICK_MODEL_RESULT=%ERRORLEVEL%"
echo Setup did not finish. See the error above.
pause
exit /b %QUICK_MODEL_RESULT%
