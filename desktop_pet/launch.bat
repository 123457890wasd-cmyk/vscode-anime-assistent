@echo off
set PYTHON=%AIRI_PYTHON_PATH%
if "%PYTHON%"=="" set PYTHON=python

cd /d "%~dp0."
echo Starting Airi Desktop Pet...
start "Airi Pet" "%PYTHON%" standalone.py
