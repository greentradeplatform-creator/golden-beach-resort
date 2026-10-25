@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem other software sometimes leaves these set; they break Python's library lookup
if defined PYTHONHOME set "GB_ORIG_PYTHONHOME=%PYTHONHOME%"
if defined PYTHONPATH set "GB_ORIG_PYTHONPATH=%PYTHONPATH%"
set "PYTHONHOME="
set "PYTHONPATH="
set "VPY=%~dp0.venv\Scripts\python.exe"
title Golden Beach Resort - Status
if not exist "%VPY%" (
  echo ยังไม่ได้ติดตั้ง ดับเบิลคลิก start.bat เพื่อเริ่ม
  pause
  exit /b 0
)
"%VPY%" "%~dp0tools\local.py" status
echo.
echo log เต็ม: logs\server.log
pause
