# Luxon Intelligence — 사용 가이드

> 로컬 LLM 4-티어 + MCP 브리지 + agentic loop 통합 스택. Claude API 의존 제거.

## 목차

1. [30초 요약](#30초-요약)
2. [최초 설정](#최초-설정)
3. [7개 CLI 명령](#7개-cli-명령)
4. [실전 시나리오](#실전-시나리오)
5. [Python API](#python-api)
6. [문제 해결](#문제-해결)
7. [바벨 전략 근거](#바벨-전략-근거)

---

## 30초 요약

노트북(140만원, Ryzen AI 7 350, 32GB, 50 TOPS NPU)에서 **로컬 LLM으로 모든 걸 하는 도구**. 한 명령으로 CUFA 보고서 · MCP 호출 · 시그널 분석.

```bash
python -m kis_backtest.luxon.intelligence bootstrap   # 스택 준비
python -m kis_backtest.luxon.intelligence ask "질문"   # 물어보기
python -m kis_backtest.luxon.intelligence cufa ...    # 보고서
```

---

## 최초 설정

### 1. 로컬 LLM 스택 기동 (Windows)

```powershell
C:\scripts\start-llm-stack.ps1
```

기동 대상: Ollama(:11434, 필수) + FastFlowLM(:52625) + KoboldCpp(:5001).

### 2. 자동 기동 Task Scheduler 등록 (선택, 로그온 시 자동)

```powershell
cd backtester
powershell -ExecutionPolicy Bypass -File scripts/install_scheduler.ps1
```

제거:
```powershell
powershell -ExecutionPolicy Bypass -File scripts/install_scheduler.ps1 -Uninstall
```

### 3. 환경변수 설정

```bash
cp .env.example .env
# .env 편집 → MCP_VPS_TOKEN 입력
```

import 시 `kis_backtest.luxon.intelligence`가 `.env` 자동 로드. 시스템 env 우선.

### 4. 작업 디렉토리

모든 CLI 명령은 `backtester/` 디렉토리에서 실행:

```bash
cd <HOME>/Desktop/02_NEXUS프로젝트/open-trading-api/backtester
```

---

## 7개 CLI 명령

### `health` — 빠른 헬스 스캔

```bash
python -m kis_backtest.luxon.intelligence health
```

```
[OK] DEFAULT   ← qwen3:14b 작동 중
[OK] HEAVY     ← gemma4:26b 작동 중
[--] FAST      ← NPU 꺼짐
[--] LONG      ← iGPU 꺼짐
```

### `security` — 보안 preflight

```bash
python -m kis_backtest.luxon.intelligence security
```

엔드포인트 감사 (loopback/HTTPS 체크) + 토큰 존재 확인 + 위험 경고.

### `bootstrap` — 전 스택 준비

```bash
python -m kis_backtest.luxon.intelligence bootstrap
```

1. 4티어 헬스체크
2. 다운 시 `start-llm-stack.ps1` 자동 호출
3. 10초 대기
4. 각 티어 워밍업 (ping 1회)
5. MCP 서버 프로빙
6. 통합 리포트

옵션:
- `--no-start` — 스크립트 자동 호출 안 함
- `--warmup-timeout 120` — 워밍업 최대 대기

### `ask` — 단일 프롬프트

```bash
python -m kis_backtest.luxon.intelligence ask "2026 조선업 전망 3문장"
python -m kis_backtest.luxon.intelligence ask "Kill Condition 3개 생성" --tier HEAVY --max-tokens 800
```

옵션:
- `--tier FAST|DEFAULT|HEAVY|LONG`
- `--max-tokens N` (응답 상한)
- `--temperature 0.0~1.0` (0=결정론적)
- `--system "역할 지정"`

### `agent` — Agentic tool-calling

```bash
python -m kis_backtest.luxon.intelligence agent \
    "삼성전자 PER 분석" \
    --tier HEAVY \
    --servers kis-backtest,nexus-finance \
    --max-steps 5
```

MCP tools 자동 수집 → LLM이 필요 tool 판단 → 호출 → 결과 피드백 → 반복 → 최종 답.

### `cufa` — CUFA 보고서 풀 빌드

```bash
python -m kis_backtest.luxon.intelligence cufa \
    --config=./samples/hhi_config.py \
    --out=./output/hhi.html
```

옵션:
- `--heavy-thesis` — §2 Thesis를 gemma4:26b로 (더 정밀)
- `--skip-health` — 헬스체크 건너뛰기

출력:
- `./output/hhi.html` (HTML)
- `./output/hhi.ticket.yaml` (Trade Ticket)

### `bench` — 3티어 성능 실측

```bash
python -m kis_backtest.luxon.intelligence bench
```

---

## 실전 시나리오

### "HD현대중공업 보고서 뽑기"

```bash
python -m kis_backtest.luxon.intelligence bootstrap
python -m kis_backtest.luxon.intelligence cufa \
    --config=./samples/hhi_config.py --out=./output/hhi.html
start ./output/hhi.html
```

### "새 종목 보고서 만들기"

```bash
cp samples/hhi_config.py samples/samsung_config.py
# 에디터에서 META/PRICE/THESIS/VALUATION_SCENARIOS/trade_ticket 수정
python -m kis_backtest.luxon.intelligence cufa \
    --config=./samples/samsung_config.py --out=./output/samsung.html
```

### "퀀트 전략 브레인스토밍"

```bash
python -m kis_backtest.luxon.intelligence ask \
    "조선주 모멘텀 전략 5가지 아이디어" \
    --tier HEAVY --max-tokens 1000
```

### "실시간 재무 분석 (VPS MCP)"

```bash
export MCP_VPS_TOKEN="..."   # 또는 .env에 저장
python -m kis_backtest.luxon.intelligence agent \
    "삼성전자 2025 PER 계산 + peer 비교" \
    --servers nexus-finance --tier HEAVY
```

---

## Python API

CLI 대신 코드로 쓰고 싶으면:

```python
from kis_backtest.luxon.intelligence import (
    Tier, call, agentic_run, health_check_all,
)
from kis_backtest.luxon.intelligence.bootstrap import bootstrap

# 단일 호출
answer = call(
    Tier.DEFAULT,
    system="간결히.",
    user="오늘 코스피 흐름 요약",
    max_tokens=300,
)

# Agentic
result = agentic_run(
    "삼성전자 PER vs peer",
    tier=Tier.HEAVY,
    mcp_servers=["kis-backtest", "nexus-finance"],
    max_steps=5,
)
print(result.final_content)

# 부트스트랩
rep = bootstrap(auto_start_stack=True)
print(rep.format_report())
```

---

## 문제 해결

| 증상 | 원인 | 해결 |
|------|------|------|
| `DEFAULT returned empty content` | qwen3 thinking 모드 토큰 소진 | `--max-tokens` 500+ |
| `FAST endpoint unreachable` | FastFlowLM 미기동 | `C:\scripts\start-flm-server.cmd` |
| `MCP nexus-finance timeout` | VPS 네트워크/토큰 | `security` 명령으로 확인 |
| `UnicodeEncodeError cp949` | Windows 콘솔 인코딩 | `chcp 65001` 또는 PowerShell 사용 |
| `plotly not installed` | 단순 경고 | `pip install plotly` 또는 무시 |
| `Evaluator FAIL 반복` | 프롬프트 키워드 누락 | `--heavy-thesis` 옵션 |

---

## 바벨 전략 근거

Taleb Antifragility: 중간값(generalist)보다 극단값(specialist at each end)이 변동성에 강함.

- **FAST** — 초경량 (항상 켜둠, 2W)
- **HEAVY** — 초정밀 (명시 요청만, Kill condition)
- **LONG** — 초장문 (수동/실험, 긴 문서)
- **DEFAULT** — 중간 허브 (일반 작업)

퀀트 포트폴리오의 barbell(현금+레버리지)과 동일 구조.

---

## 티어 선택 치트시트

| 상황 | 티어 |
|------|------|
| 자동완성·알림·뉴스 분류 | FAST |
| 일반 코딩·질문 | **DEFAULT** |
| 반증 조건·Kill condition | HEAVY |
| CUFA Business (세그먼트 풍부) | LONG |
| 모르겠음 | **DEFAULT** |

---

## 파일 구조

```
backtester/
├── kis_backtest/luxon/intelligence/
│   ├── __init__.py, __main__.py
│   ├── router.py, bootstrap.py, security.py, cli.py
│   ├── mcp_bridge.py, mcp_registry.py, agentic.py
│   ├── assemble.py, bench.py, _env.py
│   ├── prompts/            # 9 .md
│   ├── tasks/              # signal, catalyst, cufa_narrative, evaluator_repair
│   └── tests/              # 123 tests, coverage 88%
├── samples/
│   ├── __init__.py
│   └── hhi_config.py       # HD현대중공업 CUFA config 템플릿
├── scripts/
│   └── install_scheduler.ps1   # Task Scheduler 자동 등록
├── .env.example            # 환경변수 템플릿 (MCP_VPS_TOKEN 등)
└── docs/
    └── LUXON_INTELLIGENCE.md   # 이 문서
```

---

## 최종 점검 체크리스트

- [ ] Ollama `qwen3:14b`, `gemma4:26b` pull 완료
- [ ] `.env`에 `MCP_VPS_TOKEN` 입력
- [ ] `python -m kis_backtest.luxon.intelligence health` → `[OK] DEFAULT`
- [ ] `python -m kis_backtest.luxon.intelligence ask "test"` → 응답 확인
- [ ] Task Scheduler 등록 (선택)
- [ ] 샘플 CUFA 1건 빌드 → Evaluator 12/12 PASS
