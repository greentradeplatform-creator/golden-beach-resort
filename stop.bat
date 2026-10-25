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
title Golden Beach Resort - Stop
if exist "%VPY%" (
  "%VPY%" "%~dp0tools\local.py" stop
) else (
  rem no .venv yet: end any python.exe that is listening on the port
  if not defined PORT set "PORT=8000"
  for /f "tokens=5" %%P in ('netstat -ano -p TCP ^| findstr /R /C:":!PORT! .*0\.0\.0\.0:0"') do (
    tasklist /FI "PID eq %%P" /NH | findstr /I "python" >nul && taskkill /PID %%P /T /F >nul && echo [OK] ปิดโปรแกรม PID %%P แล้ว
  )
)
timeout /t 5
