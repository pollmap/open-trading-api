# Luxon Backtester — AGENTS.md (전체 인프라 포함)

OpenCode/Claude Code 공용 프로젝트 매뉴얼. 이 파일 로드 시 Luxon AI 전체 인프라 인지.

---

## 1. 사용자 & 프로젝트

**사용자**: 이찬희 (충북대 경영3-1, Luxon AI 창업자, CUFA 회장, INTP)
**기기**: LENOVO 83HY (Ryzen AI 7 350, 32GB, Radeon 860M) + Galaxy Book2 Pro (DOGE WSL)
**프로젝트**: Luxon AI 퀀트 자동화 — 개인 사용 전용 (SaaS 금지)

---

## 2. 로컬 LLM 3-티어 (자동 라우팅)

| 티어 | 엔진 | 모델 | 포트 | TPS | 용도 |
|---|---|---|---|---|---|
| FAST | FLM NPU | qwen3.5:4b | 52625 | 14.2 | 시그널·라벨·알림 |
| DEFAULT | Ollama CPU | qwen3:14b | 11434 | 5.2 | CUFA 섹션·일반 작업 |
| HEAVY | Ollama CPU | gemma4:26b | 11434 | 3-5 | Falsifiable·Kill Condition |
| LONG | KoboldCpp iGPU | gemma4-e4b | 5001 | 11.8 | 32k 장문 (수동) |

**라우터**: `kis_backtest/luxon/intelligence/router.py`
```python
from kis_backtest.luxon.intelligence import Tier, call, agentic_run
resp = call(Tier.DEFAULT, system="...", user="...")
```

---

## 3. MCP 서버 (398도구)

| 서버 | 위치 | 프로토콜 | 주요 도구 |
|---|---|---|---|
| nexus-finance | VPS 62.171.141.206/mcp | HTTP | DART/KRX/ECOS + vault_* 6 |
| kis-backtest | 로컬 :3846/mcp | HTTP | 백테스트·주가 |
| drawio | mcp.draw.io | HTTPS | 다이어그램 |
| gitlawb | WSL `gl mcp serve` | stdio | DID/git |

**config**: `C:/Users/lch68/.config/opencode/opencode.json` + `~/.mcp.json`
**토큰**: `$env:MCP_VPS_TOKEN` (nexus-finance 인증용)

---

## 4. 에이전트 인프라 (VPS 2 + WSL 1)

| 에이전트 | MBTI | 위치 | 포트 | 역할 |
|---|---|---|---|---|
| HERMES | ENTJ | VPS | 18789 | 수익+트레이딩+발행 |
| NEXUS | ENFJ | VPS | 18790 | 데이터 허브+MCP 398도구 |
| DOGE | INTP | Galaxy WSL | 18794 | 리서치+퀀트 검증 |

**SSH**:
```bash
ssh luxon              # VPS (HERMES/NEXUS)
ssh -i ~/.ssh/cbnupollmap root@62.171.141.206
ssh valuealpha@10.0.0.2    # Galaxy Book DOGE
```

**⛔ VPS 설정 변경 절대 금지** — systemd/nginx/UFW/openclaw.json 명시 요청 없이 건드리지 말 것.

---

## 5. Obsidian Vault (공유 뇌, 2,812 노트)

- 위치: `/root/obsidian-vault/` (VPS)
- 구조: Karpathy 8-layer + wiki 763
- 접근: `nexus-finance` MCP vault_* 6도구 또는 SSH
- 크론 33개, 스크립트 16개 자율 운영

---

## 6. 스킬 204개 (`~/.claude/skills/`)

**핵심 스킬**:
- `cufa-equity-report` — 보고서 v14.1, Evaluator v2 ALL PASS
- `cufa-diagram` — 다이어그램
- `quant-fund` — AI 퀀트 운용 (53 tests)
- `competition-arsenal` — 공모전/경시대회
- `gsd-workflow` — 대규모 작업
- `scrape`, `defuddle` — 웹 크롤링

스킬 로드: AGENTS.md 지시대로 `~/.claude/skills/{name}/SKILL.md` 먼저 Read 후 따를 것.

---

## 7. 메모리 시스템 (96파일)

경로: `~/.claude/projects/C--Users-lch68/memory/MEMORY.md`

**중요 메모리**:
- `user_chanhi_profile.md` — 사용자 프로필
- `feedback_extensible_ai_philosophy.md` — 3대 원칙 (Agent-Ready + 범용+개인화 + AI-Augmentable)
- `feedback_absolute_real_data.md` — 실데이터 절대 원칙
- `feedback_cufa_repo_first.md` — CUFA 레포 빌더 우선
- `feedback_mcp_first.md` — MCP 도구 우선
- `feedback_luxon_personal_use_only.md` — 개인 사용 전용
- `project_local_llm_migration_complete.md` — 로컬 LLM 완전 전환 (2026-04-13)

---

## 8. 절대 규칙

1. **CFS 전용**: 연결 재무제표만. OFS 금지.
2. **실데이터 원칙**: 목업/할루시네이션 금지. MCP 398도구 우선.
3. **Forward/Trailing 명시**: PER/PBR 표기 기준 병기.
4. **개인 사용 전용**: SaaS/멀티유저화 금지.
5. **VPS 설정 건드리지 말 것**: 명시 요청 없이 systemd/nginx/UFW 변경 금지.
6. **Read 먼저**: 파일 수정 전 반드시 전체 Read.
7. **범위 엄수**: "이 파일만" = 그 파일만. 확장 시 물어보기.
8. **Discord/Telegram 알림 금지**: 사용자 제외 요청.

---

## 9. 코드 핵심 경로

```
kis_backtest/
├── luxon/intelligence/     # 로컬 LLM 라우터 + MCP 브리지 + OpenCode client
│   ├── router.py           # 4-티어 라우팅
│   ├── agentic.py          # tool-calling 루프
│   ├── mcp_bridge.py       # JSON-RPC 2.0
│   ├── opencode_client.py  # OpenCode HTTP 래퍼
│   └── data_fallback.py    # MCP → yfinance 폴백
├── lean/                   # Docker 백테스트
├── forecasting/            # Kronos/Chronos 예측
├── providers/{kis,upbit,yfinance}/
└── execution/              # LiveOrderExecutor, risk_gateway

scripts/
├── luxon_quant_hourly.py   # 매시간 루프
├── luxon_paper_trader.py   # 페이퍼 트레이딩
├── luxon_lean_integration.py # Lean 연동
├── luxon_monthly_review.py
└── luxon_quarterly_review.py
```

---

## 10. Task Scheduler (6 태스크)

```
Luxon-FLM-NPU             로그온 (NPU 자동기동)
Luxon-KoboldCpp-iGPU      로그온 (iGPU 자동기동)
Luxon-OpenCode-Serve      매시간 (서버 헬스가드)
Luxon-Hourly-Quant        매시간 (agentic 스캔)
Luxon-Monthly-Review      일 18:00 (말일만 실행)
Luxon-Quarterly-Review    일 18:30 (분기말일만)
```

---

## 11. 커밋 원칙

- Conventional Commits: `feat(luxon): ...`, `fix(lean): ...`
- 메시지 한국어 OK
- Co-authored-by 금지
- pollmap/open-trading-api 원격 (main 브랜치)

---

## 12. 사용자 스타일

- 한국어, 반말
- 조용히 일하고 결과만
- 테이블/도식화 극대화
- 범위 엄수
- 서브에이전트 병렬 극한 추구
- 중간 보고 과다 금지

---

## 13. 참고 문서

- `docs/LUXON_INTELLIGENCE_COMPLETE.md` — 14섹션 해부서
- `docs/TOOLS_INTEGRATION.md` — MCP/Cline/Continue
- `plans/serene-launching-sparrow.md` — Sprint J 최신 계획
- `C:/Users/lch68/CLAUDE.md` — 전역 프로젝트 지침
- `C:/Users/lch68/LOCAL_LLM_STACK.md` — 로컬 LLM 스택

---

## 14. 즉시 가용한 도구

| 작업 | 명령 |
|---|---|
| 매시간 루프 수동 실행 | `python scripts/luxon_quant_hourly.py --force` |
| 월간 복기 | `python scripts/luxon_monthly_review.py --month=2026-04` |
| 페이퍼 주문 | `python scripts/luxon_paper_trader.py --ticket=...` |
| Lean 백테스트 | post_market 자동, 수동은 `luxon_lean_integration.py` |
| 테스트 | `pytest kis_backtest/luxon/intelligence/tests/ -v` (147 PASS) |
| Kronos 예측 | `from kis_backtest.forecasting import predict` |

---

**끝**. 이 파일 + CLAUDE.md + 메모리 MEMORY.md 3개가 전체 컨텍스트. 작업 시작 전 관련 부분 확인 후 진행.

<!-- CLAUDE_SYNC_START -->

## CLAUDE.md 자동 스냅샷

`C:/Users/lch68/CLAUDE.md` 내용 매시간 동기화. 원본 수정 권장.

```markdown
## 소통 원칙 (인사이트 78세션 분석, 2026.04.12)

- **조용히 일하고 결과만**: 중간 보고 과다 금지. 결과물 완성 후 보여주기. 진행 상황은 물어볼 때만.
- **범위 절대 엄수**: 찬희가 지정한 범위 밖으로 절대 확장 금지. "내 파트만" = 내 파트만. 관련 영역 보여도 물어보고 확장. (37회 wrong_approach 재발 방지)
- **VPS/인프라 설정 절대 금지**: 명시적 요청 없이 VPS config, systemd, nginx, UFW 절대 건드리지 마. (무단 서비스 중지 사건 재발 방지)

## 작업 실행 원칙

- **ONE AT A TIME**: 멀티 피처 요청 시 순서대로 하나씩 실행. 각 피처 완료 후 체크리스트 확인 → 다음으로. 절대 한번에 다 하려고 하지 말 것.
- **빠른 실행 우선**: 계획 길게 설명하지 말고 바로 실행. 막히면 바로 대안 제시
- **끝까지 차근차근**: 플랜 잡으면 순서대로 끝까지 밀고 가기. 중간에 딴 길로 새지 말 것
- **audit → fix 패턴**: 먼저 현재 상태 확인 → 문제 발견 → 즉시 수정
- **접근법 먼저 확인**: 구현 전에 3-4줄 요약 접근법 제시. 제약조건 고려. 승인 후 코딩.
- **증분 검증**: 파일 수정 후 즉시 테스트. 실패하면 다른 파일 건드리기 전에 먼저 수정.
- **세션 핸드오프**: 세션 종료 전 미완료 작업이 있으면 메모리에 저장. 완료된 것/남은 것/다음 명령어 명확히 기록. (44세션 중 41% mostly_achieved → 이어가기 개선)

## 편집 안전

- **Read 먼저**: 파일 수정 전 반드시 전체 내용 Read. 읽지 않은 파일 수정 금지.
- **문자수 검증**: 리팩토링/이동 시 before/after 문자수 비교 필수. 1% 이상 손실 → 즉시 revert.
- **대량 편집**: 복사본에서 작업 후 교체. 원본 직접 수정 최소화.

## 찬희님 작업 스타일

- **반말 캐주얼 톤**: 존댓말 X, 친구처럼 빠르게
- **결과물 테이블 정리**: 진행 현황은 테이블로 한눈에 보여주기
- **수동 작업 명확히 분리**: Claude가 할 수 있는 것 vs 찬희가 직접 해야 할 것 구분
- **메모리 적극 활용**: 보류/다음에 할 작업은 반드시 메모리에 저장
- **git commit 자주**: 작업 단위마다 commit + push

## Luxon AI 인프라 (v4.1, 2026.04.08)

2에이전트(VPS) + 1에이전트(WSL), MCP v8.0-phase14 64서버/398도구, Vault 2,812 노트 (Karpathy 8-layer, wiki 763)

| 에이전트 | MBTI | 포트 | 위치 | 역할 | 상태 |
|---------|------|------|------|------|------|
| HERMES | ENTJ | 18789 | VPS | 수익 엔진 — 트레이딩+ACP+발행 | active |
| NEXUS | ENFJ | 18790 | VPS | 팀 공유 AI — CUFA/금은동 데이터 허브 | active |
| DOGE | INTP | 18794 | WSL | 리서치+퀀트 엔진 — 딥 리서치+소스 수집+검증 (ORACLE 흡수) | WSL only |

핵심 경로:
- SOUL.md: `/root/{agent}-home/.openclaw/workspace/SOUL.md`
- MCP 서비스: `/opt/nexus-finance-mcp` (symlink: `/root/nexus-finance-mcp`)
- MCP health: `curl http://127.0.0.1:8100/health`
- Vault: `/root/obsidian-vault/` (Karpathy 8-layer, 2,812 노트, 33 크론, 16 스크립트)
- WSL SSH: `valuealpha@10.0.0.2`
- 노트북 원격: `ssh luxon claude`
- VPS: `ssh -i ~/.ssh/cbnupollmap root@62.171.141.206`

## 에이전트 역할 (혼동 금지)

- **HERMES**(ENTJ): 외부 소통+수익+발행 — 결단력 있는 실행 지휘관, 목표 지향
- **NEXUS**(ENFJ): 데이터 허브+팀 교육 — nexus-finance-mcp 398도구, Discord #luxon-general
- **DOGE**(INTP): 리서치+퀀트 검증+소스 수집 — 끝까지 파고드는 분석가 (WSL, ORACLE 기능 흡수)
- DOGE는 **WSL에서만 실행**. VPS에 잔재 서비스 있으면 즉시 중지.

보안: mcpserver 전용유저, HTTPS(443), **127.0.0.1 바인딩**, nginx auth+IP제한, UFW deny, SSH key-only
MCP 재시작 시 25초 대기 (46서버 로딩)

## Environment Notes (로컬)

- **메인 노트북**: LENOVO 83HY — AMD Ryzen AI 7 350 (8C/16T+NPU), 32GB RAM, Radeon 860M
- **현재 환경**: Windows 11 (Lenovo) + WSL2 Ubuntu 24.04 (같은 기기 내)
- **Galaxy Book2 Pro**: 별도 기기, DOGE 에이전트(INTP, :18794) 24시간 운영. SSH: valuealpha@10.0.0.2
- **WSL 구분**: Lenovo WSL2 = gitlawb CLI만. Galaxy Book WSL2 = DOGE 에이전트. 혼동 금지!
- **멀티라인 paste 금지**: 터미널에서 여러 줄 붙여넣기 시 깨짐. `&&`로 한 줄로 합치거나 파일로 작성
- **코드베이스 구분**:
  - `~/Desktop/` — 로컬 작업 (보고서, 스크립트, open-trading-api)
  - VPS (`ssh luxon claude`) — Luxon 에이전트, MCP 서버, nexus-finance-mcp
  - 에이전트 3개: VPS 2개(HERMES/NEXUS) + WSL 1개(DOGE). 로컬(Windows)에는 에이전트 없음
- **KIS 투자 인프라**: ~/Desktop/open-trading-api/, KIS config: ~/KIS/config/kis_devlp.yaml
- **MCP 로컬**: drawio (원격) + kis-backtest (127.0.0.1:3846) + nexus-finance (VPS) — ~/.mcp.json (type: "http")
- **Docker Desktop**: 설치됨, 자동 시작

## 로컬 LLM 스택 (2026-04-13 실측 완료)

3칩(NPU+iGPU+CPU) 동시 활용, 140만원 노트북에서 GPT-4o급 로컬 실행.

| 엔진 | 칩 | 포트 | 모델 | Decode TPS | 상용 동급 |
|------|-----|------|------|-----------|-----------|
| FastFlowLM v0.9.38 | NPU | 52625 | qwen3.5:4b | 14.2 | Haiku 4.5 |
| KoboldCpp v1.111.2 | iGPU 860M | 5001 | Gemma4 E4B | ~11.8 | GPT-4o mini |
| Ollama 0.20.2 | CPU | 11434 | qwen3:14b / gemma4:26b | 5.2 | Sonnet 4.5 |

- **실행**: `powershell C:\scripts\start-llm-stack.ps1`
- **라우터**: LiteLLM (포트 4000), config: `~/.config/litellm/config.yaml`
- **폴백 체인**: NPU → iGPU → CPU → Claude API
- **Ollama 860M 버그**: ollama/ollama #14562 OPEN, KoboldCpp로 우회
- **NPU 전력**: ~2-12W, 월 ₩72~432 (Claude API 대비 99%+ 절감)
- **경로**: FLM(`C:\Program Files\flm\`), KoboldCpp(`C:\tools\`), GGUF(`C:\models\`)

## OpenClaude 사용 규칙

- **--bare 모드 필수**: 203 스킬이 GPT-5 컨텍스트 윈도우 초과. `oc` 별칭은 `--bare --system-prompt-file` 자동 적용
- **경량 프롬프트**: `~/.claude/openclaude-system-prompt.md` — 핵심 규칙만
- **모델**: codexspark(GPT-5.3) 기본, codexplan(GPT-5.4) 주간 한도 내
- **별칭**: `oc`(5.3), `oc5`(5.4), `oc3`(5.3), `ocds`(DeepSeek), `oc-raw`(순수bare), `oc-full`(전체모드-크래시가능)
- **토큰 자동 리프레시**: Task Scheduler 매일 09:00, 수동: `oc-refresh`
- **gitlawb**: DID did:key:z6Mkk8..., WSL `gl` 명령, MCP 40도구

## MCP 사용 규칙 (인사이트 78세션: tool_api_failure 다수 + wrong_approach 37회)

### 도구명/파라미터 검증 필수 (세션 시작 시)
- MCP 도구 사용 전 **반드시 정확한 도구명+파라미터명 검증**. 추측/가정 금지.
- 예: `fred_get_series` ✗ → `macro_fred` ✓, `ticker` ✗ → `stock_code` ✓
- 첫 호출 전 discovery call로 실제 API shape 확인. InputValidationError 나면 즉시 파라미터 재확인.
- **세션 시작 프로토콜**: 데이터 수집 작업이면 사용할 MCP 도구 목록+파라미터 먼저 검증 → 작업 시작

### 재사용 원칙 (커스텀 구현 금지)
- **MCP 398도구에 있는 기능을 직접 구현하지 마.** pandas.corr 재구현, GothamGraph stdlib 같은 오버엔지니어링 금지.
- 구현 전 "이 기능 MCP에 있나?" 먼저 확인. 있으면 MCP 사용. 없을 때만 직접 구현.
- 기존 모듈(portfolio, risk, macro)에 있는 기능도 재구현 금지. import해서 사용.

### 데이터 수집 우선순위
1. **Nexus MCP 398도구 먼저** — 데이터가 충분하다고 판단해도 MCP 먼저 확인. 건너뛰기 금지.
2. **직접 API** (DART, KRX, KIS) — MCP에 없는 데이터만
3. **웹 스크래핑** — 1, 2 모두 불가할 때만 fallback
4. **배치 작업 시**: rate limit 걸리면 exponential backoff (2^n초, max 120초) + 진행률 저장 후 재개 가능하게

## Financial Data Rules

- **CFS 전용**: 재무제표는 반드시 연결(CFS) 사용, 별도(OFS) 절대 금지. 매출/ROE 등 핵심 수치는 연결 기준 교차검증
- **Forward/Trailing 기준 명시**: PER/PBR/EV-EBITDA 표기 시 Forward/Trailing/TTM/12MF 기준 반드시 표기

## Report Quality Targets (v14.1)

- **CUFA 보고서 공식 레포 포맷 필수**: 반드시 `cufa-equity-report` 레포의 빌더+CSS+구조를 따를 것. 커스텀 보고서 구조 새로 만들기 **절대 금지**. HD건설기계 v4-1 = CSS 표준. (20세션 중 매번 커스텀 구조 만들어서 재빌드 반복한 마찰 방지)
- **CUFA 보고서**: 80K자+, SVG 25+, 테이블 25+. Evaluator v2 ALL PASS 필수
- **Evaluator 자동 실행**: 보고서 빌드 후 반드시 `python builder/evaluator.py` 실행. FAIL 항목이 있으면 자동 수정 → 재빌드 → 재검증 루프
- **서브에이전트 차트 검증**: 하드코딩 흰 배경 금지, 빈 차트 금지, 모든 라벨/데이터 존재 필수, SVG 포맷 검증. 서브에이전트 출력은 조립 전에 반드시 검증
- **서브에이전트 완료 후 검증 필수**: HTML 열어서 모든 SVG 렌더링 확인 → 문자수 카운트(80K+) → CFS 데이터 확인 → SKILL.md 규칙 대조. 검증 실패 시 자동 수정 후 재시도

## 소통 스타일 (인사이트 78세션 분석, 2026.04.12)

- **조용히 실행, 결과만 보여주기**: 중간 과정 나열 금지. "~하겠습니다", "~확인하겠습니다" 반복 금지. 결과물 완성 후 보여주기. 진행 상황은 물어볼 때만 보고.
- **도식화 극대화**: 모든 설명에 계층도+논리흐름+시계열+워크플로우 도식 사용. 테이블, ASCII 차트, 트리 구조 적극 활용.
- **세션 시작 템플릿**: "Task: [목표]. Scope: [범위]. Constraints: [제약]." 형태로 시작. 범위 불명확하면 물어보기.
- **wrong_approach 방지 (50회/78세션)**: 작업 전 "접근법 3줄 요약" → 찬희 승인 → 코딩. 승인 없이 구현 시작 금지.
- **실데이터 절대 원칙**: 목업/가짜/할루시네이션 금지. MCP 398도구 우선. 데이터 없으면 "없다"고 명시. 출처 항상 표기.

## 범위 통제 (인사이트 78세션: wrong_approach 37회 + misunderstood_request 9회)

- **⛔ 범위 엄수 (최우선)**: 찬희가 "내 파트만", "이 파일만", "이 코드베이스만" 지정하면 **절대** 범위 확장 금지. 관련 영역이 보여도 물어보고 확장. top-10 필터링, 팀 전체 분석 같은 임의 축소/확장 금지.
- **NOT TO DO 리스트**: 작업 시작 전 "하지 말 것" 명확히 인지. 기존 인프라 재구현 금지, MCP에 있는 기능 직접 코딩 금지, 지정 범위 밖 파일 수정 금지.
- **첫 패스 꼼꼼히**: 관련 파일 ALL Read 후 작업 시작. 대충 보고 추천하지 말 것. 구체적 수치+파일경로+라인번호로 답변. "~정도", "아마" 금지
- **세션 범위 선언**: 세션 시작 시 "이번 세션 범위: [X]" 명시적 선언. 중간에 범위 추가 시 찬희 승인 필수

## 서브에이전트 품질 게이트 (인사이트 기반, 31회 버그 코드 방지)

- **서브에이전트 계약서**: 스폰 전에 입력/출력/최소 크기/검증 기준 명시
- **출력 검증 필수**: 서브에이전트 완료 후 (1) 파일 존재+비어있지 않음 (2) 문법 검증(py_compile) (3) 필수 콘텐츠 마커 grep (4) 크기 기준 충족 — 4개 중 하나라도 실패하면 재스폰
- **SKILL.md 규칙 전달**: 서브에이전트에게 프롬프트 줄 때 핵심 규칙(차트 스타일, 클래스명, 글자수) 반드시 포함. "SKILL.md 읽어라"만으로 부족 — 핵심 3줄을 직접 명시

## Remote/VPS Operations

- **⛔ VPS 설정 변경 절대 금지**: 명시적 요청 없이 config, systemd, nginx, UFW, DNS, openclaw.json 절대 수정 금지. 에이전트 서비스 중지/재시작도 찬희 승인 필수. (2026-03-29 전체 에이전트 동시 장애 사건 재발 방지)
- **SSH heredoc 안전**: Python f-string, 따옴표, 특수문자 이스케이프 철저. 확신 없으면 소규모 스니펫으로 먼저 테스트
- **heredoc 검증 프로토콜**: 파일 작성 후 반드시 `cat` 또는 `head`로 내용 확인. f-string 중괄호, 백슬래시, 달러기호가 이스케이프됐는지 검증 후 진행
- **VPS 코드베이스 확인**: 로컬 agent-nexus vs VPS nexus-finance-mcp 혼동 금지. 작업 대상 명시적 확인 후 시작
- **인코딩 (반복 마찰 원인)**: 모든 파일 I/O에 `encoding='utf-8'` 명시. cp949 절대 가정 금지. VPS/WSL: `PYTHONIOENCODING=utf-8` 필수. 한글 경로 처리 시 pathlib 사용. Windows↔WSL↔VPS 간 인코딩 불일치 발생하면 즉시 UTF-8 강제 후 진행

## Phase 체크포인트 (인사이트: 41% 세

... [생략 — 원본: C:/Users/lch68/CLAUDE.md]
```
<!-- CLAUDE_SYNC_END -->
