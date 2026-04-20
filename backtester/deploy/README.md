# Luxon Terminal — 배포 가이드 (Sprint 1.5)

24/7/365 자동화 배포 이중화 구성:
- **Windows Task Scheduler** (로컬 개발 환경) — 일일 07:00
- **VPS systemd timer** (프로덕션) — 매 시간 정각

## 🪟 Windows Task Scheduler (로컬)

### 설치

관리자 권한 PowerShell에서:
```powershell
cd <HOME>\Desktop\open-trading-api\backtester
powershell -ExecutionPolicy Bypass -File scripts\setup_scheduler_windows.ps1
```

### 확인

```powershell
Get-ScheduledTask -TaskName "LuxonFredDaemon"
Get-ScheduledTaskInfo -TaskName "LuxonFredDaemon"
```

### 수동 실행 (테스트)

```powershell
Start-ScheduledTask -TaskName "LuxonFredDaemon"
```

### 제거

```powershell
Unregister-ScheduledTask -TaskName "LuxonFredDaemon" -Confirm:$false
```

---

## 🐧 VPS systemd (프로덕션)

### 1. 파일 복사

VPS(<MCP_VPS_HOST>)에서:
```bash
# 루트 권한
sudo mkdir -p /opt/luxon-terminal
sudo git clone https://github.com/pollmap/open-trading-api /opt/luxon-terminal
cd /opt/luxon-terminal/backtester

# venv 생성
sudo python3 -m venv .venv
sudo .venv/bin/pip install -e .

# 상태/캐시/출력 디렉토리
sudo mkdir -p /var/lib/luxon/{cache/fred,state,out}
sudo mkdir -p /etc/luxon
sudo chown -R luxon:luxon /var/lib/luxon /opt/luxon-terminal

# 비밀 환경 변수 (Discord 웹훅 등)
sudo tee /etc/luxon/luxon.env <<EOF
LUXON_DISCORD_WEBHOOK=https://discord.com/api/webhooks/...
NEXUS_MCP_TOKEN=Bearer ...
EOF
sudo chmod 600 /etc/luxon/luxon.env
```

### 2. systemd 유닛 설치

```bash
sudo cp deploy/luxon-fred-daemon.service /etc/systemd/system/
sudo cp deploy/luxon-fred-daemon.timer /etc/systemd/system/
sudo systemctl daemon-reload

# 타이머 활성화 (부팅 시 자동 시작 + 즉시 시작)
sudo systemctl enable --now luxon-fred-daemon.timer
```

### 3. 상태 확인

```bash
# 타이머 다음 실행 시각
systemctl list-timers luxon-fred-daemon.timer

# 서비스 마지막 실행 결과
systemctl status luxon-fred-daemon.service

# 실시간 로그 (journalctl)
journalctl -u luxon-fred-daemon.service -f
```

### 4. 수동 실행 (테스트)

```bash
sudo systemctl start luxon-fred-daemon.service
journalctl -u luxon-fred-daemon.service --since "5 minutes ago"
```

### 5. 제거

```bash
sudo systemctl disable --now luxon-fred-daemon.timer
sudo rm /etc/systemd/system/luxon-fred-daemon.{service,timer}
sudo systemctl daemon-reload
```

---

## 📊 상태 모니터링

### 상태 파일

```
~/.luxon/state/fred_daemon.json
```

구조:
```json
{
  "last_success": "2026-04-11T07:00:15",
  "last_failure": null,
  "total_runs": 42,
  "success_count": 42,
  "failure_count": 0,
  "consecutive_failures": 0
}
```

### Obsidian Vault 스냅샷

```
~/obsidian-vault/02-Areas/trading-ops/macro/YYYY-MM-DD.md
```

매일 새 파일 생성, 10개 지표 + 대시보드 embed.

### Discord 알림

`LUXON_DISCORD_WEBHOOK` 환경 변수 설정 시:
- 🟢 성공: INFO 알림 (수집 10/10, staleness)
- 🔴 실패: CRITICAL 알림 (에러 메시지)

---

## ⚙️ 아키텍처

```
┌──────────────────────────────────────────────────────────┐
│  Windows Task Scheduler (로컬 Lenovo 83HY)               │
│  └ 매일 07:00 → luxon_macro_daemon.py --mode oneshot    │
│     ├→ FREDHub (Nexus MCP macro_fred)                    │
│     ├→ Parquet 캐시 (~/.luxon/cache/fred/)               │
│     ├→ PNG/HTML 출력 (./out/)                            │
│     ├→ Obsidian Vault 마크다운 (trading-ops/macro/)      │
│     ├→ 상태 파일 (~/.luxon/state/fred_daemon.json)       │
│     └→ AlertSystem → Discord 웹훅                        │
└──────────────────────────────────────────────────────────┘
                         ↕ (이중화, 한쪽 실패해도 다른 쪽 동작)
┌──────────────────────────────────────────────────────────┐
│  VPS systemd timer (<MCP_VPS_HOST>)                      │
│  └ 매 시간 정각 → luxon-fred-daemon.service              │
│     └ (로컬과 동일한 파이프라인)                          │
└──────────────────────────────────────────────────────────┘
```

**이중화의 가치:**
- 로컬만: 찬희 노트북 꺼져있으면 수집 중단
- VPS만: 로컬 개발 환경에서 최신 데이터 부재
- **이중화**: 둘 중 하나만 동작해도 SLO 99.9% 유지

---

## 🚨 트러블슈팅

### MCP 연결 실패

```bash
# VPS MCP 헬스체크
curl -sL http://<MCP_VPS_HOST>/mcp
# → 405 Method Not Allowed 정상 (POST만 받음)
```

### 데몬 수동 디버그

```bash
.venv/Scripts/python.exe scripts/luxon_macro_daemon.py --mode oneshot -v
```

### 상태 초기화

```bash
rm ~/.luxon/state/fred_daemon.json
rm -rf ~/.luxon/cache/fred/*.pkl
```

### Obsidian Vault 경로 변경

```bash
LUXON_VAULT_ROOT=/custom/path python scripts/luxon_macro_daemon.py --mode oneshot
```
