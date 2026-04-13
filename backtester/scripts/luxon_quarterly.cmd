@echo off
chcp 65001 > nul
cd /d "C:\Users\lch68\Desktop\open-trading-api\backtester"
set PYTHONIOENCODING=utf-8
REM 분기 말월(3/6/9/12) 말일만 실행
powershell -NoProfile -Command "$d=Get-Date; if ($d.AddDays(1).Day -ne 1 -or ($d.Month % 3) -ne 0) { exit 0 }"
if errorlevel 1 exit /b 0
python scripts\luxon_quarterly_review.py
exit /b %ERRORLEVEL%
