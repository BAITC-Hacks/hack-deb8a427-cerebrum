@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run_venv
where py >nul 2>nul
if not errorlevel 1 goto run_py
where python >nul 2>nul
if not errorlevel 1 goto run_python
echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/
echo Enable "Add python.exe to PATH" during installation, then run start.cmd again.
set "launch_exit_code=1"
goto finish

:run_venv
".venv\Scripts\python.exe" run.py %*
goto capture_exit

:run_py
py -3 run.py %*
goto capture_exit

:run_python
python run.py %*

:capture_exit
set "launch_exit_code=%errorlevel%"
:finish
if not "%launch_exit_code%"=="0" pause
exit /b %launch_exit_code%
