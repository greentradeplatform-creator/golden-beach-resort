@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONHOME="
set "PYTHONPATH="
set "VPY=%~dp0.venv\Scripts\python.exe"
title Golden Beach Resort - Choose Python
echo.
echo ============================================================
echo   เลือก Python ที่ใช้รันโปรแกรม
echo ============================================================
echo.
call "%~dp0tools\_bootpy.bat"
if not defined BOOT (
  echo [X] ไม่พบ Python ในเครื่อง รัน setup.bat เพื่อติดตั้ง
  pause
  exit /b 1
)
if not exist "%VPY%" goto :pick
for /f "delims=" %%I in ('call "%VPY%" -c "import sys; print(sys.version.split()[0], sys.base_prefix)" 2^>nul') do echo ตอนนี้ใช้: Python %%I
echo.
:pick
if not exist "%~dp0.local" mkdir "%~dp0.local"
%BOOT% "%~dp0tools\pick_python.py" --choose --write "%~dp0.local\python.txt" 2>nul
if errorlevel 1 (
  pause
  exit /b 1
)
set /p SYSEXE=<"%~dp0.local\python.txt"
echo.
echo จะสร้าง .venv ใหม่ด้วย Python ตัวนี้ (ข้อมูลการจองใน resort.db ไม่หาย)
choice /C YN /M "ยืนยัน"
if errorlevel 2 exit /b 0
if exist "%VPY%" "%VPY%" "%~dp0tools\local.py" stop 2>nul
if exist "%~dp0.venv" rmdir /s /q "%~dp0.venv"
if exist "%~dp0.venv" (
  echo [X] ลบ .venv ไม่ได้ ปิดหน้าต่างอื่นที่ใช้โปรแกรมอยู่ แล้วลองใหม่
  pause
  exit /b 1
)
set "GB_PYTHON=%SYSEXE%"
endlocal & set "GB_PYTHON=%SYSEXE%"
call "%~dp0setup.bat"
