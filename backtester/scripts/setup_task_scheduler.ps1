# Luxon Terminal — Windows Task Scheduler 자동화 설정 (v0.9 STEP 5)
#
# 장 마감 후 매일 16:05 KST에 luxon_terminal_run.py 자동 실행.
# 금요일 16:30에 주간 복기 실행.
#
# 사용법 (관리자 PowerShell):
#   .\setup_task_scheduler.ps1
#
# 제거:
#   Unregister-ScheduledTask -TaskName "Luxon-Daily-Cycle" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "Luxon-Weekly-Review" -Confirm:$false

param(
    [string]$RepoRoot = "C:\Users\lch68\Desktop\open-trading-api",
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
Write-Host "=== Luxon Task Scheduler 등록 ===" -ForegroundColor Cyan

# 1. Daily cycle task (매일 16:05 KST, 주말 제외)
$dailyScript = Join-Path $RepoRoot "backtester\scripts\luxon_terminal_run.py"
if (-not (Test-Path $dailyScript)) {
    Write-Host "경고: $dailyScript 없음 — 태스크는 등록되지만 실행은 실패할 것" -ForegroundColor Yellow
}

$dailyAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$dailyScript`" --max-cycles 1" `
    -WorkingDirectory (Join-Path $RepoRoot "backtester")

$dailyTrigger = New-ScheduledTaskTrigger -Daily -At "16:05"
$dailyTrigger.DaysOfWeek = 62  # 월(2)+화(4)+수(8)+목(16)+금(32) = 62

$dailySettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName "Luxon-Daily-Cycle" `
    -Description "Luxon Terminal 일일 cycle() 실행 (장 마감 후)" `
    -Action $dailyAction `
    -Trigger $dailyTrigger `
    -Settings $dailySettings `
    -Force | Out-Null

Write-Host "✓ Luxon-Daily-Cycle: 평일 16:05 KST" -ForegroundColor Green

# 2. Weekly review (금요일 16:30 KST)
$weeklyScript = Join-Path $RepoRoot "backtester\scripts\luxon_weekly_review.py"
$weeklyAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$weeklyScript`"" `
    -WorkingDirectory (Join-Path $RepoRoot "backtester")

$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "16:30"

Register-ScheduledTask `
    -TaskName "Luxon-Weekly-Review" `
    -Description "Luxon 주간 복기 + WeeklyReport 생성" `
    -Action $weeklyAction `
    -Trigger $weeklyTrigger `
    -Settings $dailySettings `
    -Force | Out-Null

Write-Host "✓ Luxon-Weekly-Review: 금요일 16:30 KST" -ForegroundColor Green

# 3. Walk-Forward 월간 검증 (매월 말일 17:00)
$wfScript = Join-Path $RepoRoot "backtester\scripts\run_walk_forward.py"
$wfAction = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$wfScript`" --equity-file data\equity_curve.json --auto-promote --ladder-state data\ladder_state.json" `
    -WorkingDirectory (Join-Path $RepoRoot "backtester")

$wfTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "17:00"

Register-ScheduledTask `
    -TaskName "Luxon-Monthly-WF" `
    -Description "Luxon Walk-Forward 검증 + CapitalLadder 자동 승급" `
    -Action $wfAction `
    -Trigger $wfTrigger `
    -Settings $dailySettings `
    -Force | Out-Null

Write-Host "✓ Luxon-Monthly-WF: 금요일 17:00 KST (주간 WF 검증)" -ForegroundColor Green

Write-Host "`n=== 등록 완료 ===" -ForegroundColor Cyan
Write-Host "확인: Get-ScheduledTask -TaskName 'Luxon-*'"
Write-Host "수동 실행: Start-ScheduledTask -TaskName 'Luxon-Daily-Cycle'"
Write-Host "로그: Event Viewer > Windows Logs > Application"
