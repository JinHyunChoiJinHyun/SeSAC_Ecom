@echo off
REM Remove the collector tasks from Windows Task Scheduler. Scripts and collected data are kept.
schtasks /Delete /F /TN "SeSAC_Ecom\youtube_views_daily"
schtasks /Delete /F /TN "SeSAC_Ecom\external_weekly"
