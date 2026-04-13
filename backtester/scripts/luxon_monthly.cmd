@echo off
chcp 65001 > nul
cd /d "C:\Users\lch68\Desktop\02_NEXUS프로젝트\open-trading-api\backtester"
set PYTHONIOENCODING=utf-8
REM 매월 말일만 실행 체크
powershell -NoProfile -Command "if ((Get-Date).AddDays(1).Day -ne 1) { exit 0 }"
if errorlevel 1 exit /b 0
python scripts\luxon_monthly_review.py
exit /b %ERRORLEVEL%
