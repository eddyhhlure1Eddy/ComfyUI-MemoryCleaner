@echo off
chcp 65001 >nul
echo ========================================
echo RAM Memory Cleaner
echo Author: eddy
echo ========================================
echo.

cd /d "%~dp0"

py cleanup_ram.py

echo.
pause
