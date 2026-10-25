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
title Golden Beach Resort - Reset demo data
if not exist "%VPY%" goto :needsetup
"%VPY%" "%~dp0tools\local.py" needs-setup >nul 2>&1
if errorlevel 1 goto :needsetup
goto :ready
:needsetup
call "%~dp0setup.bat" /auto
if errorlevel 1 exit /b 1
:ready
echo คำเตือน: จะลบการจอง ลูกค้า ผู้ใช้ และรูปที่อัปโหลดทั้งหมดในเครื่องนี้
echo แล้วสร้างข้อมูลตัวอย่างชุดใหม่ (ไม่กระทบเว็บที่ขึ้นโฮสต์จริง)
choice /C YN /M "สำรองข้อมูลเดิมก่อน แล้วล้างข้อมูลเลยไหม"
if errorlevel 2 (
  echo ยกเลิกแล้ว
  timeout /t 5
  exit /b 0
)
if exist "%~dp0resort.db" "%VPY%" "%~dp0tools\local.py" backup
"%VPY%" "%~dp0tools\local.py" reset-demo
echo เปิดเว็บใหม่ด้วย start.bat
pause
