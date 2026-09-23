@echo off
REM ===================================================================
REM  Live2D probe - REAL target environment (pywebview / WebView2)
REM
REM  KEEP THIS FILE PURE ASCII (same reason as 1_run_browser.bat).
REM  Chinese output is written by Python into UTF-8 .txt files, which
REM  this script prints and opens in Notepad at the end.
REM
REM  Two passes, because they answer two different questions:
REM    pass A - normal opaque window, on-page log visible
REM             Q: does WebView2 give us a working WebGL context at all?
REM    pass B - frameless + transparent + always-on-top
REM             Q: does the real desktop-pet window config also render?
REM ===================================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=C:\Users\Mr.hancard\.workbuddy\binaries\python\envs\live2d_probe\Scripts\python.exe"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PROBE_PAGE=probe.html"
set "PROBE_MANUAL=1"
set "PROBE_WEBVIEW_ARGS="

if not exist "%PY%" (
  echo [ERROR] python venv not found:
  echo         %PY%
  echo         Create that venv and install pywebview first, then retry.
  pause
  exit /b 1
)

echo ============================================================
echo  Live2D probe  -  real target environment (WebView2)
echo ============================================================
echo.
echo  pass A : normal opaque window with the on-page log visible.
echo           This tells us whether WebView2 can do WebGL at all.
echo  pass B : frameless + transparent + always-on-top, exactly the
echo           config desktop_pet uses.
echo.
echo  In both passes the window closes itself. Where you are asked
echo  to press a key, press it in THIS console window.
echo.
pause

REM ---------------- pass A : plain opaque window ---------------------
set "PROBE_PORT=19901"
set "PROBE_TRANSPARENT=0"
set "PROBE_ONTOP=0"
set "PROBE_FRAMELESS=0"
set "PROBE_HOLD=40"
set "PROBE_TIMEOUT=70"
set "PROBE_LABEL=pass A opaque"
set "PROBE_SHOWLOG=1"

echo.
echo ============================================================
echo  [A] launching - a NORMAL window should appear
echo      If the model renders you will see the whale girl.
echo      If it fails, a red error log is shown inside the window.
echo ============================================================
"%PY%" "%~dp0run_webview.py"
if exist "%~dp0webview_result.txt" copy /y "%~dp0webview_result.txt" "%~dp0result_A_opaque.txt" >nul

echo.
echo -------- pass A result --------
if exist "%~dp0result_A_opaque.txt" type "%~dp0result_A_opaque.txt"
echo.
echo Press any key to run pass B (transparent desktop-pet config) ...
pause >nul

REM ---------------- pass B : real desktop-pet config -----------------
set "PROBE_PORT=19902"
set "PROBE_TRANSPARENT=1"
set "PROBE_ONTOP=1"
set "PROBE_FRAMELESS=1"
set "PROBE_HOLD=40"
set "PROBE_TIMEOUT=70"
set "PROBE_LABEL=pass B transparent"

echo.
echo ============================================================
echo  [B] launching - the model should FLOAT on your desktop
echo      with no window frame and no border of any kind.
echo      Nothing visible means transparent rendering failed.
echo ============================================================
"%PY%" "%~dp0run_webview.py"
if exist "%~dp0webview_result.txt" copy /y "%~dp0webview_result.txt" "%~dp0result_B_transparent.txt" >nul

echo.
echo ============================================================
echo  RESULTS
echo ============================================================
echo.
echo -------- pass A (opaque) --------
if exist "%~dp0result_A_opaque.txt" type "%~dp0result_A_opaque.txt"
echo.
echo -------- pass B (transparent) --------
if exist "%~dp0result_B_transparent.txt" type "%~dp0result_B_transparent.txt"
echo.
echo The same text is saved as result_A_opaque.txt and
echo result_B_transparent.txt next to this script; Notepad opens B now.
echo.
if exist "%~dp0result_B_transparent.txt" start "" notepad "%~dp0result_B_transparent.txt"
pause
