@echo off
REM ===================================================================
REM  Live2D probe - browser preview (most reliable route, look here first)
REM
REM  KEEP THIS FILE PURE ASCII.
REM  cmd.exe parses a .bat using the OEM codepage (936 / GBK on a
REM  Chinese Windows). A UTF-8 .bat therefore gets mis-decoded, the
REM  mangled bytes eat the following newline, and cmd then tries to
REM  run chinese fragments as commands. All user-facing Chinese lives
REM  in the UTF-8 .txt result files written by the Python side instead.
REM ===================================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe"
set "PROBE_PORT=19890"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "%PY%" (
  echo [ERROR] python venv not found:
  echo         %PY%
  echo         Create that venv and install pywebview first, then retry.
  pause
  exit /b 1
)

echo ============================================================
echo  Live2D probe  -  browser preview
echo ============================================================
echo  This route is already verified on this machine:
echo    WebGL 2.0 / ANGLE / RTX 5060 / D3D11 - model renders,
echo    idle motion + expressions + motion switch - all OK.
echo.
echo  port %PROBE_PORT%
echo.
echo [1/2] starting local static server ...
start "Live2D probe server - close this window to stop" "%PY%" "%~dp0serve.py"

echo       waiting for the server to come up ...
timeout /t 4 /nobreak >nul

echo [2/2] opening the probe page in your default browser ...
start "" "http://127.0.0.1:%PROBE_PORT%/probe.html?showcase=1"

echo.
echo  A blue-haired whale girl should appear and keep playing idle.
echo  To stop the server, close the window titled
echo  "Live2D probe server - close this window to stop".
echo.
pause
