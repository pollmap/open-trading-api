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
