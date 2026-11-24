@echo off
setlocal
set PYTHONNOUSERSITE=1
title VFX Pipeline Hub — Windows Setup
cd /d "%~dp0"

if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" -s "%~dp0setup_client.py" %*
) else (
    python "%~dp0setup_client.py" %*
)

echo.
pause
