@echo off
REM Luxon Quant Hourly — Task Scheduler 호출 대상
REM 매시간 실행, 장중(09:00-15:30)/장후(15:30-18:00)/장전(08:00-09:00)만 작동

chcp 65001 > nul
cd /d "C:\Users\lch68\Desktop\open-trading-api\backtester"

set PYTHONIOENCODING=utf-8
set LUXON_HOURLY=1

python scripts\luxon_quant_hourly.py %*

exit /b %ERRORLEVEL%
