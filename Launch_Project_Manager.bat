@echo off
setlocal
set PYTHONNOUSERSITE=1
title VFX Project Manager
if exist "%~dp0python\pythonw.exe" (
    start "" "%~dp0python\pythonw.exe" -s "%~dp0project_manager.py" %*
) else (
    where pythonw >nul 2>&1 && (
        start "" pythonw "%~dp0project_manager.py" %*
    ) || (
        python "%~dp0project_manager.py" %*
    )
)
