# Luxon Quant - Windows Task Scheduler 등록
# 매일 16:00 KST 일일 복기, 매주 금 16:30 주간 복기
#
# 실행: PowerShell -ExecutionPolicy Bypass -File scripts\setup_scheduler.ps1
# 확인: schtasks /query /TN "LuxonQuant*"
# 삭제: schtasks /delete /TN "LuxonQuant-DailyReview" /F

$PythonPath = (Get-Command python).Source
$ScriptDir = Split-Path -Parent $PSScriptRoot
$BacktesterDir = Join-Path $ScriptDir "backtester"
$DailyScript = Join-Path $BacktesterDir "scripts\run_daily_review.py"
$PaperScript = Join-Path $BacktesterDir "scripts\run_paper_trading.py"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Luxon Quant - Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Python:  $PythonPath"
Write-Host "Script:  $DailyScript"
Write-Host ""

# 1. 매일 16:00 일일 복기
Write-Host "[1/2] 일일 복기 스케줄 등록 (매일 16:00)..." -ForegroundColor Yellow
schtasks /create /TN "LuxonQuant-DailyReview" `
    /TR "$PythonPath $DailyScript --force" `
    /SC DAILY `
    /ST 16:00 `
    /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Host "  FAIL (관리자 권한 필요?)" -ForegroundColor Red
}

# 2. 매주 금요일 16:30 주간 복기
Write-Host "[2/2] 주간 복기 스케줄 등록 (매주 금 16:30)..." -ForegroundColor Yellow
schtasks /create /TN "LuxonQuant-WeeklyReview" `
    /TR "$PythonPath $DailyScript --weekly --force" `
    /SC WEEKLY `
    /D FRI `
    /ST 16:30 `
    /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "  OK" -ForegroundColor Green
} else {
    Write-Host "  FAIL (관리자 권한 필요?)" -ForegroundColor Red
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  등록 확인:" -ForegroundColor Cyan
Write-Host "  schtasks /query /TN `"LuxonQuant-DailyReview`"" -ForegroundColor Gray
Write-Host "  schtasks /query /TN `"LuxonQuant-WeeklyReview`"" -ForegroundColor Gray
Write-Host ""
Write-Host "  삭제:" -ForegroundColor Cyan
Write-Host "  schtasks /delete /TN `"LuxonQuant-DailyReview`" /F" -ForegroundColor Gray
Write-Host "  schtasks /delete /TN `"LuxonQuant-WeeklyReview`" /F" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan
