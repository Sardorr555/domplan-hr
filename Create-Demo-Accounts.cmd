@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Start Start-HR.cmd first to install the local environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py --seed
echo Save the generated passwords privately. Existing passwords are not reset.
pause
