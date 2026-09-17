@echo off
rem stereo-tv launcher for Windows. First run: windows\setup-windows.ps1 (or this will offer to run it).
cd /d "%~dp0.."
if not exist "python\python.exe" (
    echo First run: setting up a portable Python and the dependencies...
    powershell -NoProfile -ExecutionPolicy Bypass -File "windows\setup-windows.ps1" || exit /b 1
)
"python\python.exe" -m stereotv --windowed %*
