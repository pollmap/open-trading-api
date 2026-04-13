# Luxon Terminal — AGENTS.md

> OpenCode / Claude Code / Aider 등 AI 코드 에이전트가 이 저장소에서 작업할 때 참고할 컨텍스트.
> 일반 기여자는 [CONTRIBUTING.md](CONTRIBUTING.md) 참조.

---

## 프로젝트 개요

**Luxon Terminal** — Python 기반 AI 퀀트 운용 시스템.

- 데이터: 한국투자증권(KIS) Open API + 로컬 MCP (확장 가능)
- 전략: Ackman–Druckenmiller (집중 + 매크로) + Walk-Forward OOS
- 실행: `LiveOrderExecutor` + `RiskGateway` (9-gate) + `CapitalLadder` (5-stage)
- 피드백: 선순환 3-루프 (Weekly → conviction / Kill → KillSwitch / TA → probability)

---

## 레이어별 핵심 모듈

| Layer | Path | 역할 |
|---|---|---|
| Data | `kis_backtest/providers/{kis,upbit,yfinance}/` | 거래소 API 래퍼 |
| Analysis | `kis_backtest/portfolio/macro_regime.py` | 10-indicator 매크로 레짐 |
| Graph | `kis_backtest/luxon/graph/` | GothamGraph (symbol/sector/person/theme) |
| Orchestration | `kis_backtest/luxon/orchestrator.py` | run_workflow 진입점 |
| Execution | `kis_backtest/execution/` | Order / Risk / Ladder / Fill |
| Intelligence | `kis_backtest/luxon/intelligence/` | MCP bridge + LLM router (optional) |
| Observability | `scripts/luxon_server.py` | Phosphor Dashboard (:7777) |
| Feedback | `kis_backtest/portfolio/feedback_adapter.py` | 선순환 종점 |

엔트리포인트: `kis_backtest.luxon.terminal.LuxonTerminal` — boot → cycle → run_loop.

---

## 작업 원칙 (에이전트용)

1. **Read first, edit second**: 파일 수정 전 반드시 전체 Read. 추측 금지.
2. **Test-driven**: 신규 기능 = 테스트 먼저 (`tests/test_*.py`).
3. **Immutable by default**: `@dataclass(frozen=True)` 선호.
4. **Errors at boundaries**: 외부 I/O는 예외 처리, 내부 로직은 trust.
5. **CFS-only**: 재무제표는 연결(CFS) 기준. 별도(OFS) 금지.
6. **No hardcoded secrets**: 모든 키/토큰은 env var 또는 `~/KIS/config/kis_devlp.yaml`.
7. **Korean market specifics**: 세율·호가·TR ID 는 `strategies/risk/cost_model.py` + `providers/kis/constants.py` 에 집중.

---

## 테스트

```bash
pytest tests/ -q                          # 전체
pytest tests/test_cufa_conviction.py -v   # 모듈별
pytest -m integration                     # MCP 실서버 필요
```

기대 결과: 950+ PASS, integration은 skip (서버 안 떠있으면).

---

## 커밋 메시지

[Conventional Commits](https://www.conventionalcommits.org/):

```
feat(luxon): ...
fix(execution): ...
refactor(portfolio): ...
test(core): ...
docs(readme): ...
```

---

## 민감 파일 (절대 커밋 금지)

`.gitignore` 에 등록된 항목 참고:

- `kis_devlp.yaml` (KIS API 키)
- `.env` / `.env.*`
- `fills/` (실 체결 내역)
- `data/ladder_state.json` (자본 배포 상태)
- `~/.luxon/` (런타임 상태 전체)
- `KIS/` 디렉토리

---

## MCP 통합 (선택)

Luxon은 MCP(Model Context Protocol) 서버와 통합 가능하나 **필수 아님**.

- 기본: `http://127.0.0.1:8100` (로컬 MCP 설치 필요)
- 오버라이드: `MCP_HOST` 환경변수
- MCP 없어도 동작: `yfinance` fallback + KIS 직접 호출

---

## 참고 문서

- [README.md](README.md) — 사용자용 퀵스타트
- [ARCHITECTURE.md](ARCHITECTURE.md) — 7계층 구조 + 선순환 루프
- [CONTRIBUTING.md](CONTRIBUTING.md) — 기여 가이드
- [SECURITY.md](SECURITY.md) — 보안 정책
- [CHANGELOG.md](CHANGELOG.md) — 버전 이력
