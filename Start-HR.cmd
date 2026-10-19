@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run: preparing the Python environment...
  python -m venv .venv
  if errorlevel 1 goto error
)
if not exist ".venv\installed-local.txt" (
  ".venv\Scripts\python.exe" -m pip install -r requirements-local.txt
  if errorlevel 1 goto error
  echo ready>".venv\installed-local.txt"
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 goto error
exit /b 0
:error
echo Could not start DOMPLAN HR. Check the error above.
pause
exit /b 1
