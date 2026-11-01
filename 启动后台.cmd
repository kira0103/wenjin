@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先按 README 的首次安装说明准备 Python 环境。
  pause
  exit /b 1
)
call npm start
if errorlevel 1 pause
