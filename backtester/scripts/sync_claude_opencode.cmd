@echo off
chcp 65001 > nul
cd /d "C:\Users\lch68\Desktop\02_NEXUS프로젝트\open-trading-api\backtester"
set PYTHONIOENCODING=utf-8
python scripts\sync_claude_opencode.py
exit /b %ERRORLEVEL%
