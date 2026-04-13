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
        Trigger = "AtLogOn"
    },
    @{
        Name   = "Luxon-KoboldCpp-iGPU"
        Script = "C:\scripts\start-kobold-server.cmd"
        Desc   = "KoboldCpp iGPU server (gemma4-e4b)"
        Trigger = "AtLogOn"
    },
    @{
        Name   = "Luxon-Hourly-Quant"
        Script = "C:\Users\lch68\Desktop\02_NEXUS프로젝트\open-trading-api\backtester\scripts\luxon_hourly.cmd"
        Desc   = "Luxon Quant Hourly Loop (agentic scan, Simons eval)"
        Trigger = "Hourly"
    },
    @{
        Name   = "Luxon-Monthly-Review"
        Script = "C:\Users\lch68\Desktop\02_NEXUS프로젝트\open-trading-api\backtester\scripts\luxon_monthly.cmd"
        Desc   = "Luxon 월간 복기 (매월 말일 18:00)"
        Trigger = "Monthly"
    },
    @{
        Name   = "Luxon-Quarterly-Review"
        Script = "C:\Users\lch68\Desktop\02_NEXUS프로젝트\open-trading-api\backtester\scripts\luxon_quarterly.cmd"
        Desc   = "Luxon 분기 복기 (분기 말일 18:30)"
        Trigger = "Quarterly"
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
    if ($t.Trigger -eq "Hourly") {
        $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) `
            -RepetitionInterval (New-TimeSpan -Hours 1) `
            -RepetitionDuration (New-TimeSpan -Days 365)
    } elseif ($t.Trigger -eq "Monthly") {
        $trigger = New-ScheduledTaskTrigger -Daily -At 18:00
    } elseif ($t.Trigger -eq "Quarterly") {
        $trigger = New-ScheduledTaskTrigger -Daily -At 18:30
    } else {
        $trigger = New-ScheduledTaskTrigger -AtLogOn
    }
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
