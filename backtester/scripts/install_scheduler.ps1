# Luxon Local LLM Stack — Windows Task Scheduler 자동 등록.
# 로그온 시 FLM/KoboldCpp 자동 기동.
#
# 실행: powershell -ExecutionPolicy Bypass -File install_scheduler.ps1
# 제거: powershell -ExecutionPolicy Bypass -File install_scheduler.ps1 -Uninstall

param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

$Tasks = @(
    @{
        Name   = "Luxon-FLM-NPU"
        Script = "C:\scripts\start-flm-server.cmd"
        Desc   = "FastFlowLM NPU server (qwen3.5:4b)"
    },
    @{
        Name   = "Luxon-KoboldCpp-iGPU"
        Script = "C:\scripts\start-kobold-server.cmd"
        Desc   = "KoboldCpp iGPU server (gemma4-e4b)"
    }
)

if ($Uninstall) {
    foreach ($t in $Tasks) {
        try {
            Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false -ErrorAction Stop
            Write-Host "[removed] $($t.Name)"
        } catch {
            Write-Host "[skip] $($t.Name) not registered"
        }
    }
    exit 0
}

foreach ($t in $Tasks) {
    if (-not (Test-Path $t.Script)) {
        Write-Warning "Script missing: $($t.Script). Skipping $($t.Name)"
        continue
    }

    $action  = New-ScheduledTaskAction -Execute $t.Script
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    try {
        Register-ScheduledTask `
            -TaskName $t.Name `
            -Description $t.Desc `
            -Action $action `
            -Trigger $trigger `
            -Settings $settings `
            -RunLevel Limited `
            -Force | Out-Null
        Write-Host "[installed] $($t.Name) -> $($t.Script)"
    } catch {
        Write-Error "Failed to register $($t.Name): $_"
    }
}

Write-Host ""
Write-Host "Done. 로그온 시 자동 기동됩니다."
Write-Host "수동 실행: Start-ScheduledTask -TaskName Luxon-FLM-NPU"
Write-Host "상태 확인: Get-ScheduledTask -TaskName Luxon-*"
