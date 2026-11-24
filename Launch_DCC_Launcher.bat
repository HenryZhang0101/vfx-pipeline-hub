@echo off
setlocal
set PYTHONNOUSERSITE=1
title VFX DCC Launcher
if exist "%~dp0python\pythonw.exe" (
    start "" "%~dp0python\pythonw.exe" -s "%~dp0dcc_launcher.py" %*
) else (
    where pythonw >nul 2>&1 && (
        start "" pythonw "%~dp0dcc_launcher.py" %*
    ) || (
        python "%~dp0dcc_launcher.py" %*
    )
)
