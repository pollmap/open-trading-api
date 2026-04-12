# Luxon Terminal — 1인 AI 헤지펀드 개인 도구

> **871 tests** | **MCP 398 tools** | **GothamGraph 6/5** | **Phase 4 완료**

기존 `portfolio/` 17 모듈 + Nexus MCP 398 도구 + GothamGraph 지식 그래프를 `orchestrator.py` 한 파일로 연결.
터미널에서 한 줄 치면 **"이 종목 살까 말까 + 얼마나 + 왜"** 가 마크다운으로 나온다.

```
  찬희 터미널
       │
       ▼
  LuxonOrchestrator.run_workflow(["005930", "000660", ...])
       │
       ├── ① MCP 매크로 지표 → regime="recovery" (100%)
       ├── ② CUFA 보고서 자동 주입 → 인물/섹터/테마 노드
       ├── ③ Ackman+Druckenmiller 평가 → BUY/SKIP 결정
       ├── ④ Half-Kelly 포지션 사이징 → 20% / 20M KRW
       ├── ⑤ GothamGraph 교차참조 → "HBM4 양산, 이재용, 반도체"
       │
       ▼
  마크다운 리포트 + 주간 레터 + 그래프 HTML
```

---

## 사용법

```bash
cd backtester

# 빠른 스냅샷 (3초, MCP 없음)
.venv/Scripts/python.exe -m kis_backtest.luxon 005930 000660 035420

# 상세 리포트 (20초, MCP + CUFA + 그래프)
.venv/Scripts/python.exe scripts/luxon_run.py

# 주간 레터 저장
.venv/Scripts/python.exe -m kis_backtest.luxon \
  --weekly ~/Desktop/luxon/letters/2026-W15.md 005930 000660

# Task Scheduler 등록 (금요일 18:00 자동, 1회)
PowerShell -ExecutionPolicy Bypass -File scripts/setup_luxon_scheduler.ps1
```

---

## 파일 구조

```
kis_backtest/luxon/
├── orchestrator.py              핵심 — 17모듈 조합 셸 + generate_weekly_letter
├── __main__.py                  CLI: python -m kis_backtest.luxon
├── graph/                       GothamGraph 지식 그래프
│   ├── graph.py                   6노드/5엣지 + all_nodes/all_edges + 1-hop neighbors
│   ├── nodes.py                   SYMBOL/SECTOR/EVENT/THEME/MACRO_REGIME/PERSON
│   ├── edges.py                   BELONGS_TO/CATALYST_FOR/HOLDS/CORRELATED/TRIGGERED_BY
│   ├── ingestors/
│   │   ├── phase1_ingestor.py       주문제안 → EventNode
│   │   ├── catalyst_ingestor.py     카탈리스트 → EventNode + CATALYST_FOR
│   │   ├── cufa_ingestor.py         CUFA 보고서 → Person/Sector/Theme
│   │   └── correlated_ingestor.py   종목 상관관계 → CORRELATED 엣지
│   ├── parsers/
│   │   └── cufa_html_parser.py      CUFA HTML → CufaReportDigest (symbol_hint fallback)
│   └── viz/
│       └── html_renderer.py         PyVis 0.3.2 시각화 → HTML
├── stream/                      TickVault 틱데이터 저장소
│   ├── tick_vault.py              일별 pickle, append/flush/load_day/prune
│   ├── replay.py                  동기/비동기 재생
│   └── ...
└── integration/                 Phase1Pipeline 연결
    └── ...

scripts/
├── luxon_run.py                 상세 리포트 (MCP 자동 + CUFA 주입 + 그래프)
└── setup_luxon_scheduler.ps1    금요일 18:00 자동실행

tests/luxon/                     135 tests green
```

---

## GothamGraph 온톨로지

```
  6 노드                               5 엣지
  SYMBOL   (종목)    ── BELONGS_TO ──→  SECTOR  (섹터)
  EVENT    (이벤트)  ── CATALYST_FOR ─→  SYMBOL
  PERSON   (인물)    ── HOLDS ────────→  SYMBOL
  THEME    (테마)    ── CORRELATED ──↔  SYMBOL  (양방향)
  MACRO    (매크로)  ── TRIGGERED_BY ─→  EVENT
```

---

## 기존 자산 재사용 (portfolio/ 17 모듈)

| 모듈 | Luxon 에서의 역할 |
|---|---|
| `ackman_druckenmiller.py` | `evaluate_portfolio` — BUY/SKIP/HOLD 결정 |
| `catalyst_tracker.py` | `add/score` — 카탈리스트 등록 + 점수 |
| `conviction_sizer.py` | `size_position` — Half-Kelly 포지션 크기 |
| `macro_regime.py` | `fetch_indicators` — MCP 매크로 지표 로드 |
| `mcp_data_provider.py` | `health_check_sync` — Nexus MCP 398 도구 연결 |
| `investor_letter.py` | 백테스트 리포트용 (Luxon 은 summary() 직접 사용) |

---

## Sprint 히스토리

| Sprint | 커밋 | 산출물 |
|---|---|---|
| 1 | `58d6a3e` | FRED Macro Terminal + 24/7 Daemon |
| 2-2.5 | `7b7fd72`~`7bb99b4` | MacroRegime R11 수리, 10/10 지표 복구 |
| 3 | `49f4458` | TickVault pickle 기반 틱 저장소 |
| 4 | `33ca958`~`ca527ec` | Phase1Pipeline + ConvictionBridge |
| 5 | `b702685` | GothamGraph 6노드/5엣지 + 3-hop BFS |
| 6 | `933fe61` | Catalyst/CUFA Ingestors + PyVis HTML |
| 7 | `f026e8c` | CorrelatedIngestor (pandas.corr) |
| 8 | `3079650` | CUFA HTML Parser (bs4 + heuristic) |
| 9 | `53e5f22` | LuxonOrchestrator (17모듈 조합 셸) |
| Action B/C/D | `e7b173d` | CLI + 실전 스크립트 + 주간 레터 |
| MCP 통합 | `8250001` | regime confidence 0% → 100% |
| CUFA 주입 | `417ac76` | Desktop 12개 보고서 자동 주입 |
| 리뷰 수정 | `cbcce4a`~`a4c528f` | 코드 리뷰 7건 수정 |

---

## 테스트

```bash
# Luxon 전용 (135 tests)
.venv/Scripts/python.exe -m pytest tests/luxon/ -v

# 전체 회귀 (871 tests)
.venv/Scripts/python.exe -m pytest tests/ -x --tb=short
```

---

## 설계 원칙

1. **기존 자산 무수정** — portfolio/, execution/, providers/ 수정 금지
2. **MCP 우선** — 새 분석 함수 작성 전 Nexus MCP 398 도구 먼저 확인
3. **포크 우선** — 새 모듈 작성 전 external library 후보 1-2분 검색
4. **실데이터 절대** — 목업/가짜/할루시네이션 금지
5. **300 LOC 상한** — 새 파일 신규 추가 시 초과 금지
6. **개인 사용 전용** — SaaS/멀티유저/API화 금지 (상업화는 나중)

---

## 라이선스

작성자: 이찬희 (Luxon AI 창업자, CUFA 회장)
레포: [pollmap/open-trading-api](https://github.com/pollmap/open-trading-api)
