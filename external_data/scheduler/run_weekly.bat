@echo off
REM Weekly: Naver shopping -> YouTube keyword candidates -> Musinsa crawling (external_data\)
REM If one step fails, the next steps still run. Exit code 1 if any step failed.
REM Log: <project root>\logs\external_data\weekly_YYYYMMDD.log
setlocal
REM Python: the PY environment variable if set (e.g. setx PY "C:\path\to\python.exe"), otherwise python on PATH
if not defined PY set "PY=python"
cd /d "%~dp0.."
set "LOGDIR=%~dp0..\..\logs\external_data"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%d"
set "LOG=%LOGDIR%\weekly_%TODAY%.log"
set "FAILED=0"

call :step naver_keyword_trend.py
call :step youtube_keyword_candidates.py
call :step musinsa_crawler.py

echo ===== [%date% %time%] done, failed=%FAILED% >> "%LOG%"
exit /b %FAILED%

:step
echo ===== [%date% %time%] %~1 >> "%LOG%"
"%PY%" -X utf8 %~1 >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo ===== [%date% %time%] %~1 exit code %RC% >> "%LOG%"
if not "%RC%"=="0" set "FAILED=1"
exit /b 0
