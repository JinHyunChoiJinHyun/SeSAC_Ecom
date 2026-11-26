@echo off
REM Daily: YouTube keyword total views (external_data\youtube_keyword_views.py)
REM Log: <project root>\logs\external_data\daily_YYYYMMDD.log
setlocal
REM Python: the PY environment variable if set (e.g. setx PY "C:\path\to\python.exe"), otherwise python on PATH
if not defined PY set "PY=python"
cd /d "%~dp0.."
set "LOGDIR=%~dp0..\..\logs\external_data"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%d"
set "LOG=%LOGDIR%\daily_%TODAY%.log"

echo ===== [%date% %time%] youtube_keyword_views.py >> "%LOG%"
"%PY%" -X utf8 youtube_keyword_views.py >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo ===== [%date% %time%] exit code %RC% >> "%LOG%"
exit /b %RC%
