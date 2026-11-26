@echo off
REM Register the collectors in Windows Task Scheduler (folder: SeSAC_Ecom)
REM   youtube_views_daily : every day 09:00        -> run_daily.bat
REM   external_weekly     : every Monday 10:00     -> run_weekly.bat
REM Runs only while this user is logged on (no password is stored). Locked screen is OK.
REM Python: set the PY user environment variable once, e.g. setx PY "C:\path\to\python.exe" (otherwise python on PATH is used).
schtasks /Create /F /TN "SeSAC_Ecom\youtube_views_daily" /SC DAILY /ST 09:00 /TR "\"%~dp0run_daily.bat\""
schtasks /Create /F /TN "SeSAC_Ecom\external_weekly" /SC WEEKLY /D MON /ST 10:00 /TR "\"%~dp0run_weekly.bat\""
REM schtasks cannot set these: run on battery, keep running if unplugged, run a missed start as soon as possible.
powershell -NoProfile -NonInteractive -Command "$s=New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable; foreach($t in 'youtube_views_daily','external_weekly'){ Set-ScheduledTask -TaskPath '\SeSAC_Ecom\' -TaskName $t -Settings $s | Out-Null }"
schtasks /Query /TN "SeSAC_Ecom\youtube_views_daily"
schtasks /Query /TN "SeSAC_Ecom\external_weekly"
