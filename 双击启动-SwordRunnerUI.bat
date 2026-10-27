@echo off
setlocal
title Sword Audiobook Studio
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
echo [SwordRunnerUI] starting...
echo [SwordRunnerUI] url=http://127.0.0.1:8765
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-SwordRunnerUI.ps1"
echo.
echo [SwordRunnerUI] exited. Press any key to close.
pause >nul
