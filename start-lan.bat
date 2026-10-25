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
title Golden Beach Resort - Start (phone test)
if not exist "%VPY%" goto :needsetup
"%VPY%" "%~dp0tools\local.py" needs-setup >nul 2>&1
if errorlevel 1 goto :needsetup
goto :ready
:needsetup
call "%~dp0setup.bat" /auto
if errorlevel 1 exit /b 1
:ready
echo เปิดเว็บให้มือถือ/แท็บเล็ตที่ต่อ Wi-Fi เดียวกันเข้าทดสอบได้
"%VPY%" "%~dp0tools\local.py" stop >nul
"%VPY%" "%~dp0tools\local.py" start --lan
echo.
echo พิมพ์ที่อยู่ http://...:8000 ด้านบนในเบราว์เซอร์มือถือ
pause
