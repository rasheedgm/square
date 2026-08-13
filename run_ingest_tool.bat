@echo off
title Square VFX - Plate Ingest Tool Launcher
set PYTHON_EXE=%~dp0env\Scripts\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=%~dp0env\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

"%PYTHON_EXE%" "%~dp0tools\ingest_tool\main.py" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)
