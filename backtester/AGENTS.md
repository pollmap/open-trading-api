# Luxon Backtester — AGENTS.md

OpenCode/Claude Code 공용 프로젝트 매뉴얼. 이 파일을 읽고 작업 시작.

## 프로젝트 정체

Luxon AI 퀀트 자동화 플랫폼 개인용. KIS + Lean Docker + 로컬 LLM 3티어(FLM NPU / Ollama CPU / KoboldCpp iGPU) + Nexus MCP 398도구.

실제 매매만 수동, 나머지 전자동:
- 매시간 LLM 스캔 → 티켓 → 페이퍼 주문
- post_market Lean Docker 백테스트
- 월/분기 Kronos 예측 + Simons 12원칙 복기

## 절대 규칙

1. **CFS 전용**: 연결 재무제표만. 별도(OFS) 금지.
2. **실데이터 원칙**: 목업/할루시네이션 금지. MCP 398도구 우선.
3. **Forward/Trailing 명시**: PER/PBR 표기 시 기준 병기.
4. **개인 사용 전용**: SaaS/멀티유저화 금지.
5. **VPS 설정 건드리지 말 것**: 명시 요청 없이 systemd/nginx/UFW 변경 금지.
6. **Read 먼저**: 파일 수정 전 반드시 전체 Read.

## 핵심 경로

- `kis_backtest/luxon/intelligence/` — 로컬 LLM 라우터 (FAST/DEFAULT/HEAVY/LONG)
- `kis_backtest/lean/` — Docker 백테스트 엔진
- `kis_backtest/forecasting/` — Kronos/Chronos 예측
- `kis_backtest/providers/{kis,upbit,yfinance}/` — 데이터/주문
- `kis_backtest/execution/` — 주문 실행 (LiveOrderExecutor)
- `scripts/luxon_quant_hourly.py` — 매시간 루프 엔트리
- `scripts/luxon_paper_trader.py` — 페이퍼 트레이딩
- `scripts/luxon_monthly_review.py` / `luxon_quarterly_review.py` — 복기

## 로컬 LLM 티어 사용법

```python
from kis_backtest.luxon.intelligence import Tier, call, agentic_run

# FAST 빠른 라벨/알림 (FLM NPU qwen3.5:4b)
resp = call(Tier.FAST, system="...", user="...")

# DEFAULT 일반 섹션 생성 (Ollama qwen3:14b, ctx 16k)
# HEAVY 정밀 추론 (Ollama gemma4:26b, ctx 8k)
# LONG 장문 전담 (KoboldCpp gemma4-e4b, ctx 32k) — 수동만

# Agentic tool-calling (MCP 398도구)
result = agentic_run(prompt, tier=Tier.DEFAULT, mcp_servers=["kis-backtest"])
```

## 테스트

```bash
pytest kis_backtest/luxon/intelligence/tests/ -v
# 142 PASS 유지 필수
```

80% 커버리지 미만이면 PR 금지.

## 커밋 원칙

- Conventional Commits: `feat(luxon): ...`, `fix(lean): ...`
- 메시지 한국어 OK
- Co-authored-by 금지 (사용자 설정)

## MCP 서버

- `kis-backtest` — 로컬 :3846, 백테스트/백데이터
- `nexus-finance` — VPS :8100 HTTPS, 398도구 (MCP_VPS_TOKEN 필요)
- `drawio` — 다이어그램

## 금지 사항

- Discord/Telegram 알림 추가 금지 (사용자 제외)
- SaaS/멀티유저/API화 금지
- 에이전트 인프라 (HERMES/NEXUS/DOGE) config 수정 금지
- `_var` 등 기존 인프라 재구현 금지 — MCP 우선 재사용

## 사용자 스타일

- 한국어, 반말 OK
- 조용히 일하고 결과만
- 테이블/도식화 극대화
- 범위 엄수: "이 파일만" 하면 그 파일만

## 참고 문서

- `docs/LUXON_INTELLIGENCE_COMPLETE.md` — 14섹션 해부서
- `docs/TOOLS_INTEGRATION.md` — MCP/Cline/Continue
- `plans/serene-launching-sparrow.md` — Sprint J 최신 계획
