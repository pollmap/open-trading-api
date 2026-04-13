# Luxon 도구 통합 가이드 — 4-툴 풀스택 실전

> 2026-04-13 | Continue + Cline + Luxon Intelligence + OpenClaude 통합 운용

---

## 도구 매트릭스

| 도구 | 설치 상태 | 용도 | 실행 |
|------|---------|------|------|
| **Continue.dev** | ✅ v1.2.22 (VS Code) | 코딩 자동완성·채팅·편집 | VS Code 내부 `Ctrl+I` / `Ctrl+L` |
| **Cline** | ✅ v3.78.0 (VS Code) | 자율 에이전트 (파일+터미널+MCP) | VS Code Cline 패널 |
| **Luxon Intelligence** | ✅ v1.0 | CUFA 보고서·퀀트·MCP 오케스트레이션 | `python -m kis_backtest.luxon.intelligence` |
| **OpenClaude** | ✅ v0.1.8 | GPT-5.x Claude Code 워크플로우 | `oc` (shell alias) |

---

## 역할 분담 (중복 최소화)

```
┌─────────────────────────────────────────────────────────┐
│ 상황                        →  도구                     │
├─────────────────────────────────────────────────────────┤
│ 코드 작성 중 자동완성        →  Continue (NPU qwen3.5:4b) │
│ "이 함수 뭐야?" 빠른 Q&A     →  Continue 채팅 (qwen3:14b)│
│ "이 파일 리팩토링"            →  Cline 자율 모드 (26b)    │
│ "프로젝트 전체 버그 찾기"     →  Cline + MCP              │
│ CUFA 보고서 빌드             →  Luxon Intelligence cufa  │
│ MCP 398도구 탐색             →  Cline + nexus-finance    │
│ 퀀트 시그널 생성              →  Luxon ask --tier FAST   │
│ Simons 프로토콜 평가         →  Luxon tasks.simons       │
│ GPT-5 필요 (쿼터 있을 때)    →  oc (OpenClaude)          │
└─────────────────────────────────────────────────────────┘
```

---

## 각 도구 설정 확인

### Continue.dev (VS Code)

**설정 파일**: `~/.continue/config.yaml`

```yaml
models:
  - 4개 로컬 LLM (NPU/CPU×2/iGPU)
autocomplete:
  model: "NPU - Qwen3.5 4B (autocomplete)"
mcpServers:
  - kis-backtest / nexus-finance / drawio
contextProviders:
  - file / code / diff / terminal / problems / currentFile
```

**사용법**:
- `Ctrl+L` — 채팅 패널
- `Ctrl+I` — 인라인 편집
- `@file` / `@code` / `@terminal` — 컨텍스트 삽입
- 자동완성 — 타이핑만 해도 NPU 모델이 제안

### Cline (VS Code)

**설정 파일**: `~/AppData/Roaming/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`

```json
{
  "mcpServers": {
    "kis-backtest": { "url": "http://127.0.0.1:3846/mcp" },
    "nexus-finance": { "url": "https://62.171.141.206:8100/mcp",
                        "headers": { "Authorization": "Bearer ${MCP_VPS_TOKEN}" } },
    "drawio": { "url": "https://mcp.draw.io/mcp" }
  }
}
```

**사용법**:
1. VS Code 좌측 사이드바 Cline 아이콘 클릭
2. Provider 설정 → **Ollama** 선택 → Base URL: `http://localhost:11434` → Model: `qwen3:14b`
3. 자율 모드 활성화 후 작업 지시
4. MCP 탭에서 도구 목록 확인 (kis-backtest/nexus-finance/drawio)

**첫 실행 시**:
- Provider: Ollama (다른 AI API 키 불필요)
- Model: `qwen3:14b` (기본) 또는 `gemma4:26b` (복잡 작업)
- Context size: 16384 (qwen3:14b) / 8192 (gemma4:26b)
- Auto-approval: 주의! 처음엔 OFF, 안정화 후 선택적 ON

### Luxon Intelligence (CLI)

```bash
cd "C:/Users/lch68/Desktop/02_NEXUS프로젝트/open-trading-api/backtester"

# 7 서브명령 전부
python -m kis_backtest.luxon.intelligence {bootstrap|health|security|bench|ask|agent|cufa}
```

**특화 기능**:
- CUFA 보고서 12/12 PASS 자동 루프
- Simons Protocol 평가 (`tasks.simons.evaluate_trade_ticket()`)
- bootstrap 전 스택 자동 기동

### OpenClaude

```bash
oc              # GPT-5.3 spark
oc5             # GPT-5.4 plan
ocds            # DeepSeek
oc-model ollama # 로컬 Ollama 전환
oc-status       # 토큰·쿼터 확인
```

**언제 쓰나**:
- Claude Code 워크플로우에 익숙 + GPT-5 쿼터 남아있을 때
- 429 에러 시 → `oc-model ollama`로 즉시 로컬 전환

---

## 통합 워크플로우 — 실제 하루 시나리오

### 아침: 기업분석

```bash
# 1. 로컬 스택 워밍업 (부팅 후 1회)
python -m kis_backtest.luxon.intelligence bootstrap

# 2. 삼성전자 CUFA 보고서
cp samples/hhi_config.py samples/samsung_config.py
# → META/PRICE/THESIS 편집

python -m kis_backtest.luxon.intelligence cufa \
    --config=./samples/samsung_config.py \
    --out=./output/samsung.html

# 3. 생성된 보고서 열기
start ./output/samsung.html
```

### 점심: 전략 아이디어 브레인스토밍

VS Code 열고:
- Cline 패널 → "조선주 모멘텀 전략 5개 아이디어 + 백테스트 코드 초안"
- Cline이 `kis-backtest` MCP로 과거 데이터 호출 → 코드 생성 → 실행

### 오후: 코딩

VS Code에서 Python 파일 편집:
- Continue 자동완성 (NPU) — 함수 시그니처 타이핑 → 자동 완성
- `Ctrl+L` → "이 함수 타입 힌트 추가" → qwen3:14b 즉시 응답
- 복잡한 리팩토링은 Cline에게 자율 위임

### 저녁: Simons 복기

```python
from kis_backtest.luxon.intelligence.tasks.simons import evaluate_trade_ticket
import yaml

with open("./output/samsung.ticket.yaml") as f:
    ticket = yaml.safe_load(f)
import samples.samsung_config as cfg

result = evaluate_trade_ticket(ticket, cfg.__dict__)
print(f"Simons Score: {result.simons_score}/100")
print(f"추천: {result.recommendation}")
for c in result.checks:
    print(f"  {c.principle}. {c.status}: {c.reason}")
```

---

## MCP 서버 공통 접근

4개 도구 모두 **같은 MCP 서버**에 연결:
- kis-backtest (:3846)
- nexus-finance (VPS, Bearer 토큰)
- drawio

**토큰 1회만 설정**:
```powershell
# 시스템 영구 (관리자 PowerShell)
[System.Environment]::SetEnvironmentVariable('MCP_VPS_TOKEN', '실제값', 'User')
# 또는 .env 파일 (backtester/.env)
```

---

## 429 / 쿼터 대비 폴백 플랜

OpenClaude/Claude Code가 쿼터 소진 시:

1. **즉시 전환**: `oc-model ollama` → 로컬만
2. **영구 전환**: `.profile` export 수정
3. **대안 도구 사용**:
   - VS Code 작업 → Cline (완전 로컬)
   - CUFA 빌드 → Luxon Intelligence (완전 로컬)
   - 자동완성 → Continue (완전 로컬)
4. **클라우드 쿼터 대기 중**: 아침 한국시간 09:00 ChatGPT Pro 리셋 (24h 기준)

---

## 성능 팁

### Continue 자동완성이 느릴 때
- NPU(qwen3.5:4b)는 14.2 TPS. 인터넷 검색보다 빠름
- 느리면 FastFlowLM 프로세스 확인: `curl http://127.0.0.1:52625/v1/models`
- FLM 꺼져있으면 `C:\scripts\start-flm-server.cmd` 실행

### Cline이 튕길 때
- qwen3:14b tool-calling 안정적. gemma4:26b 더 정밀
- Context size 초과 경고 시 Cline settings → Context compression ON
- MCP timeout 시 `nexus-finance` 대신 `kis-backtest` 먼저

### Luxon CUFA 빌드가 오래 걸릴 때
- `force_all_heavy=False` (기본값, 하이브리드)
- `--skip-health` 로 헬스체크 건너뛰기 (이미 워밍업된 상태)
- repair_loop `max_iterations=2` 로 줄이기 (품질 trade-off)

---

## 체크리스트 — 최종 점검

- [x] VS Code 설치 (`winget install Microsoft.VisualStudioCode`)
- [x] Continue 확장 설치 (`continue.continue v1.2.22`)
- [x] Cline 확장 설치 (`saoudrizwan.claude-dev v3.78.0`)
- [x] `~/.continue/config.yaml` — 4 모델 + 3 MCP + 컨텍스트
- [x] Cline `cline_mcp_settings.json` — 3 MCP
- [x] Luxon Intelligence — 123 tests PASS, 실 CUFA 12/12 증명
- [x] OpenClaude `oc` 별칭 + `oc-model ollama` 폴백
- [ ] VS Code 첫 실행 → Cline Provider Ollama 설정
- [ ] `MCP_VPS_TOKEN` 시스템 env 등록 (관리자 필요)
- [ ] 실 Cline으로 MCP tool 호출 1회 검증

---

## 참고 파일

- `docs/LUXON_INTELLIGENCE_COMPLETE.md` — Luxon 해부서 (14섹션)
- `docs/LUXON_INTELLIGENCE.md` — 기본 사용 가이드
- `~/.continue/config.yaml` — Continue 설정
- `~/AppData/Roaming/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` — Cline MCP
- `.env.example` — 환경변수 템플릿
- `scripts/install_scheduler.ps1` — Task Scheduler 자동화
- `prompts/simons_protocol.md` — Simons 12원칙
- `tasks/simons.py` — Simons 평가 태스크
