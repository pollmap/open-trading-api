# Luxon Terminal — Windows Task Scheduler 등록 스크립트 (Sprint 1.5)
# 매일 07:00에 luxon_macro_daemon.py oneshot 실행
#
# 사용법 (관리자 권한 필요):
#   powershell -ExecutionPolicy Bypass -File scripts/setup_scheduler_windows.ps1
#
# 등록 후 확인:
#   Get-ScheduledTask -TaskName "LuxonFredDaemon"
#
# 수동 실행:
#   Start-ScheduledTask -TaskName "LuxonFredDaemon"
#
# 제거:
#   Unregister-ScheduledTask -TaskName "LuxonFredDaemon" -Confirm:$false

$ErrorActionPreference = "Stop"

$TaskName = "LuxonFredDaemon"
$BacktestRoot = Split-Path -Parent $PSScriptRoot  # backtester/
$PythonExe = Join-Path $BacktestRoot ".venv\Scripts\python.exe"
$DaemonScript = Join-Path $PSScriptRoot "luxon_macro_daemon.py"
$LogDir = Join-Path $env:USERPROFILE ".luxon\logs"

# 로그 디렉토리 생성
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    Write-Host "로그 디렉토리 생성: $LogDir"
}

# Python 실행 파일 검증
if (-not (Test-Path $PythonExe)) {
    Write-Error "Python 실행 파일 미존재: $PythonExe"
    Write-Error "backtester/.venv를 먼저 생성하세요 (uv sync 또는 python -m venv .venv)"
    exit 1
}

if (-not (Test-Path $DaemonScript)) {
    Write-Error "데몬 스크립트 미존재: $DaemonScript"
    exit 1
}

# 작업 정의
$Action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "`"$DaemonScript`" --mode oneshot" `
    -WorkingDirectory $BacktestRoot

# 트리거: 매일 07:00
$Trigger = New-ScheduledTaskTrigger -Daily -At 07:00

# 설정
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15)

# 주체: 현재 사용자, 로그인 여부 무관
$Principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U `
    -RunLevel Limited

# 설명
$Description = @"
Luxon Terminal Sprint 1.5 — FRED Macro Daemon
매일 07:00에 Nexus MCP macro_fred 도구 경유로 10개 거시 지표를 수집하고
Obsidian Vault에 스냅샷을 저장합니다.

로그: $LogDir\fred_daemon.log
상태: $env:USERPROFILE\.luxon\state\fred_daemon.json
"@

# 기존 작업 제거 (있으면)
try {
    $Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($Existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "기존 작업 제거: $TaskName"
    }
} catch {}

# 등록
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description $Description | Out-Null

Write-Host ""
Write-Host "================================================================"
Write-Host "✅ Windows Task Scheduler 등록 완료: $TaskName"
Write-Host "================================================================"
Write-Host ""
Write-Host "스케줄: 매일 07:00"
Write-Host "명령: $PythonExe `"$DaemonScript`" --mode oneshot"
Write-Host "작업 디렉토리: $BacktestRoot"
Write-Host ""
Write-Host "다음 작업:"
Write-Host "  즉시 실행 테스트: Start-ScheduledTask -TaskName $TaskName"
Write-Host "  상태 확인:        Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "  제거:             Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ""
