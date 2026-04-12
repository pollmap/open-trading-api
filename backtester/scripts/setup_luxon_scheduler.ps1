# Luxon Terminal 주간 레터 자동화 — Windows Task Scheduler 등록
# 사용: PowerShell -ExecutionPolicy Bypass -File scripts\setup_luxon_scheduler.ps1
#
# 매주 금요일 18:00 에 luxon_run.py 자동 실행.
# 결과: ~/Desktop/luxon/letters/YYYY-Www.md + out/luxon_watchlist.html

$taskName = "Luxon Weekly Letter"
$pythonPath = "$PSScriptRoot\..\..\.venv\Scripts\python.exe"
$scriptPath = "$PSScriptRoot\luxon_run.py"
$workDir = "$PSScriptRoot\.."

# 기존 동일 이름 작업 제거
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

# 트리거: 매주 금요일 18:00
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At "18:00"

# 액션: python luxon_run.py
$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument $scriptPath `
    -WorkingDirectory $workDir

# 설정
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

# 등록
Register-ScheduledTask `
    -TaskName $taskName `
    -Trigger $trigger `
    -Action $action `
    -Settings $settings `
    -Description "Luxon Terminal 주간 투자 리포트 자동 생성 (찬희 개인용)"

Write-Host "[OK] '$taskName' 등록 완료. 매주 금요일 18:00 자동 실행." -ForegroundColor Green
Write-Host "확인: Get-ScheduledTask -TaskName '$taskName'"
Write-Host "수동 실행: Start-ScheduledTask -TaskName '$taskName'"
Write-Host "삭제: Unregister-ScheduledTask -TaskName '$taskName'"
