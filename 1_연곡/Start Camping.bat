@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD goto :nopython
%PYCMD% -c "import sys; assert sys.version_info >= (3, 10)" 2>nul || goto :nopython

if not exist ".camping-venv\Scripts\python.exe" %PYCMD% -m venv .camping-venv || goto :fail
set "PY=%CD%\.camping-venv\Scripts\python.exe"
"%PY%" -m pip install -q -r requirements.txt || goto :fail
"%PY%" -m playwright install chromium || goto :fail
"%PY%" setup_account.py || goto :fail
"%PY%" configure.py --first-run || goto :fail
"%PY%" camping_watch.py %*
if errorlevel 1 goto :fail
goto :end

:nopython
echo.
echo Python 3.10 or newer is required. Install from https://www.python.org/downloads/windows/
echo (check "Add python.exe to PATH" in the installer), then run this file again.
goto :fail

:fail
echo.
echo Something failed. Read the message above, or see 01_문제생기면.txt
:end
pause
