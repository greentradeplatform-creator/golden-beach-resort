@echo off
rem Finds any Python 3.8+ just to run tools\pick_python.py, which then picks the best one.
rem Sets BOOT. Called with "call" from setup.bat / choose-python.bat (no setlocal here).
set "BOOT="
for %%C in ("py -3" "python" "py") do (
  if not defined BOOT (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
    if !errorlevel! equ 0 set "BOOT=%%~C"
  )
)
exit /b 0
