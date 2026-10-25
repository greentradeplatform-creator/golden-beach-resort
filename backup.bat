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
title Golden Beach Resort - Backup
if not exist "%VPY%" goto :needsetup
"%VPY%" "%~dp0tools\local.py" needs-setup >nul 2>&1
if errorlevel 1 goto :needsetup
goto :ready
:needsetup
call "%~dp0setup.bat" /auto
if errorlevel 1 exit /b 1
:ready
"%VPY%" "%~dp0tools\local.py" backup
pause
