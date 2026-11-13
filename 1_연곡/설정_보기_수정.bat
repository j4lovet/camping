@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".camping-venv\Scripts\python.exe" (
  echo Run "Start Camping.bat" once first to install.
  pause
  exit /b 1
)
".camping-venv\Scripts\python.exe" configure.py
pause
