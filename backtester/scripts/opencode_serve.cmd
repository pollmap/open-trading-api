@echo off
chcp 65001 > nul
cd /d "C:\Users\lch68\Desktop\02_NEXUS프로젝트\open-trading-api\backtester"
REM 이미 떠있으면 종료
powershell -NoProfile -Command "if ((Invoke-WebRequest -Uri http://127.0.0.1:4870/global/health -UseBasicParsing -TimeoutSec 2 -ErrorAction SilentlyContinue).StatusCode -eq 200) { exit 0 }" 2>nul
if errorlevel 1 start "" /B opencode.cmd serve --port 4870 --hostname 127.0.0.1
exit /b 0
