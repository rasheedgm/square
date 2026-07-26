@echo off
title Square VFX - Plate Ingest Tool Launcher
echo Launching Square VFX Ingest Tool using local Conda environment...
"%~dp0env\python.exe" "%~dp0tools\ingest_tool\main.py" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
