@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_web.ps1"
set ERR=%ERRORLEVEL%
if not %ERR%==0 (
  echo.
  echo Start selhal, kod %ERR%. Podivejte se na cache\web.log a cache\web.err
  pause
)
endlocal
