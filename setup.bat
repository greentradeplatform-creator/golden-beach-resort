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
title Golden Beach Resort - Setup
set "AUTO=%~1"
echo.
echo ============================================================
echo   Golden Beach Resort : ติดตั้งโปรแกรมสำหรับทดสอบในเครื่อง
echo ============================================================
echo.

rem ---- 1) find every Python on this computer and pick the best one for this project
if not exist "%VPY%" goto :findpy
"%VPY%" -c "import sys" >nul 2>&1
if errorlevel 1 goto :findpy
for /f "delims=" %%I in ('call "%VPY%" -c "import sys; print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%I"
echo [OK] ใช้ .venv เดิม Python %PYVER% - ถ้าต้องการเปลี่ยนเวอร์ชัน ใช้ choose-python.bat
goto :haveenv
:findpy
call "%~dp0tools\_bootpy.bat"
if not defined BOOT goto :nopython
if not exist "%~dp0.local" mkdir "%~dp0.local"
echo กำลังค้นหาและทดสอบ Python ทุกตัวในเครื่อง ...
echo.
%BOOT% "%~dp0tools\pick_python.py" --write "%~dp0.local\python.txt" 2>nul
if errorlevel 1 goto :nopython
set /p SYSEXE=<"%~dp0.local\python.txt"
echo.

rem ---- 2) private environment in .venv so nothing touches the rest of the computer
if exist "%VPY%" (
  "%VPY%" -c "import sys" >nul 2>&1
  if errorlevel 1 (
    echo .venv เดิมใช้งานไม่ได้ กำลังสร้างใหม่ ...
    rmdir /s /q "%~dp0.venv"
  )
)
if not exist "%VPY%" (
  echo กำลังสร้าง virtual environment ในโฟลเดอร์ .venv ...
  if not exist "%~dp0logs" mkdir "%~dp0logs"
  "%SYSEXE%" -m venv "%~dp0.venv" 2>"%~dp0logs\setup.log"
  if errorlevel 1 (
    type "%~dp0logs\setup.log"
    echo [X] สร้าง .venv ไม่สำเร็จ
    goto :fail
  )
)

:haveenv
rem ---- 3) libraries
echo กำลังติดตั้งไลบรารี (ครั้งแรกใช้เวลา 1-3 นาที ต้องต่ออินเทอร์เน็ต) ...
if not exist "%~dp0logs" mkdir "%~dp0logs"
"%VPY%" -m pip install --disable-pip-version-check -q --upgrade pip 2>>"%~dp0logs\setup.log"
"%VPY%" -m pip install --disable-pip-version-check -q -r "%~dp0requirements-local.txt" 2>>"%~dp0logs\setup.log"
if errorlevel 1 (
  type "%~dp0logs\setup.log"
  echo [X] ติดตั้งไลบรารีไม่สำเร็จ ตรวจอินเทอร์เน็ต แล้วรัน setup.bat อีกครั้ง
  goto :fail
)
echo.
echo ตรวจระบบก่อนใช้งาน ...
"%VPY%" "%~dp0tools\local.py" check
if errorlevel 1 (
  echo.
  echo [X] ยังใช้งานไม่ได้ ดูวิธีแก้ด้านบน
  goto :fail
)
"%VPY%" "%~dp0tools\local.py" mark-installed 2>nul
echo [OK] ติดตั้งเรียบร้อย
echo.
if /i "%AUTO%"=="/auto" exit /b 0
choice /C YN /M "เปิดเว็บเลยไหม"
if errorlevel 2 (
  echo เปิดเว็บภายหลังด้วย start.bat
  pause
  exit /b 0
)
endlocal
call "%~dp0start.bat"
exit /b 0

:nopython
echo [X] ไม่พบ Python 3.10 ถึง 3.14 แบบ 64-bit ที่ใช้งานได้ในเครื่องนี้
echo.
where winget >nul 2>&1
if errorlevel 1 goto :manualpy
choice /C YN /M "ให้ติดตั้ง Python 3.12 อัตโนมัติด้วย winget เลยไหม"
if errorlevel 2 goto :manualpy
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :manualpy
echo.
echo ติดตั้ง Python แล้ว ให้ปิดหน้าต่างนี้ แล้วดับเบิลคลิก start.bat ใหม่อีกครั้ง
pause
exit /b 1
:manualpy
echo ติดตั้ง Python จาก https://www.python.org/downloads/
echo ตอนติดตั้งให้ติ๊ก "Add python.exe to PATH" แล้วรัน start.bat ใหม่
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:fail
pause
exit /b 1
