# Luxon Terminal — Architecture & Design

> **버전**: v1.0 | **테스트**: 960+ PASS | **MCP 도구** (옵션): 398개

---

## 1. 한 문장 요약

**MCP 금융 도구로 데이터를 수집하고, 학술 기반 리스크 모듈로 검증하고, 개인투자자 제약을 반영해서, 증권사 API로 주문을 실행하는 AI 퀀트 운용 오픈소스 시스템.**

---

## 2. 철학 & 원칙

```
┌─────────────────────────────────────────────────────────────┐
│                    5대 설계 원칙                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. 가짜 데이터 절대 금지                                    │
│     모든 숫자는 MCP API 실시간 데이터에서.                   │
│     AI가 숫자를 "생성"하면 실패.                             │
│                                                              │
│  2. 비용이 알파를 먹는다 (Renaissance 교훈)                  │
│     거래비용 모델이 시스템의 심장.                            │
│     한국 RT 0.23% — 고빈도 전략 = 자살.                     │
│                                                              │
│  3. 반증 가능성                                              │
│     모든 투자 가설에 Kill Condition 명시.                     │
│     틀렸을 때 어디서 틀렸는지 추적 가능해야 한다.            │
│                                                              │
│  4. LLM = 리서처, 트레이더 X (AlphaForgeBench)              │
│     AI는 팩터 발굴/분석만.                                   │
│     실시간 매매 판단은 인간(찬희).                           │
│                                                              │
│  5. 살아남는 것이 수익보다 중요하다 (Ed Thorp)               │
│     Half-Kelly 필수. 2×Kelly = 파산.                         │
│     Millennium DD 규칙: 5%→경고, 7.5%→축소, 10%→청산.       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

**학술 기반**: Renaissance Technologies, Millennium Management, AQR, López de Prado, Moskowitz (2012), Kang et al. (2019, KAIST), Kritzman & Li (2010)

---

## 3. 시스템 계층도

```
┌─────────────────────────────────────────────────────────────────┐
│ L5: 사용자 인터페이스                                            │
│                                                                  │
│   Claude Code (로컬)                                             │
│     /quant-fund [preset]    — 퀀트 분석 + 포트폴리오             │
│     /cufa-report [ticker]   — 기업 심층 분석 (80K자+)            │
│     /kis-team               — 전략→백테스트→실행 풀 파이프라인   │
│     /finance-report         — MCP 398도구 데이터 리포트          │
│     /macro-dashboard        — 거시경제 10대 지표                 │
│                                                                  │
├──────────────────────────────────┬──────────────────────────────┤
│ L4: 데이터 수집                  │ L4b: 로컬 MCP                │
│                                  │                               │
│ Nexus Finance MCP (VPS)          │ KIS Backtest MCP              │
│ 62.171.141.206:8100              │ 127.0.0.1:3846                │
│ 398 도구 / 64 서버               │ 10 프리셋 전략                │
│                                  │                               │
│ ┌────┬────┬────┬────┬────┐      │ ┌──────────────────┐          │
│ │KRX │DART│ECOS│FRED│크립│      │ │ SMA, Momentum,   │          │
│ │ 38 │ 52 │ 18 │ 24 │ 29│      │ │ Volatility, etc. │          │
│ └────┴────┴────┴────┴────┘      │ └──────────────────┘          │
│ + 대체(53) + 퀀트(38)           │                               │
│ + viz(33) + val(10) + 뉴스(26)  │                               │
│                                  │                               │
├──────────────────────────────────┴──────────────────────────────┤
│ L3.5: 오케스트레이션 (v0.2α 신규)                               │
│                                                                  │
│ UniverseBuilder        — 6섹터 자동 종목 선별 (stocks_search     │
│   (universe_builder.py)  + dart_financial_ratios → ROE/OPM/DTE)  │
│                                                                  │
│ StrategyComparison     — N개 전략 동일 유니버스 백테스트 비교    │
│   (strategy_comparison   + BL/HRP 포트폴리오 최적화 비교         │
│    .py)                  + Sharpe/MDD 기준 랭킹 테이블           │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│ L3: 분석 엔진 (MCP 도구 조합)                                   │
│                                                                  │
│ factor_score()           — 팩터 스코어링 (momentum+value+...)    │
│ portadv_black_litterman()— 포트폴리오 최적화 (뷰 반영)          │
│ portadv_hrp()            — 계층적 리스크 패리티                  │
│ portadv_rmt_clean()      — 상관행렬 노이즈 제거 (Marchenko-Pastur)│
│ stat_arb_ou_fit()        — 페어트레이딩 OU 프로세스              │
│ cquant_funding_rate()    — 크립토 펀딩레이트 차익                │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│ L2: 리스크 관리 (kis_backtest/strategies/risk/)                  │
│                                                                  │
│ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐              │
│ │ cost_model   │ │drawdown_guard│ │ vol_target   │              │
│ │              │ │              │ │              │              │
│ │ 한국 거래세  │ │ Millennium   │ │ EWMA λ=0.94 │              │
│ │ KOSPI 0.20%  │ │ 5%→경고     │ │ 목표 10%     │              │
│ │ After-cost   │ │ 7.5%→축소   │ │ max_lev 1.5x │              │
│ │ Kelly        │ │ 10%→청산    │ │ 터뷸런스     │              │
│ │  11 tests    │ │  11 tests   │ │  8 tests     │              │
│ └──────────────┘ └──────────────┘ └──────────────┘              │
│                                                                  │
│          ┌─────────────────────────────────┐                    │
│          │      리스크 게이트 (7항목)       │                    │
│          │  Sharpe ≥ 0.5                   │                    │
│          │  MaxDD ≥ -20%                   │                    │
│          │  종목 ≤ 15%, 섹터 ≤ 35%         │                    │
│          │  상관 < 0.6, 터뷸런스 < 5x      │                    │
│          │  총 투자금 ≤ 찬희 승인 한도     │                    │
│          │  → ALL PASS 필요                │                    │
│          └─────────────────────────────────┘                    │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│ L1: 실행 (kis_backtest/portfolio/)                               │
│                                                                  │
│ ┌──────────────┐    ┌──────────────┐    ┌──────────────┐        │
│ │ mcp_bridge   │───→│ mcp_connector│───→│ PortfolioOrder│       │
│ │ MCP→KIS 변환 │    │ 결과 정규화  │    │ 종목별 지시서 │       │
│ └──────────────┘    └──────────────┘    └───────┬──────┘        │
│                                                  │               │
│                                    ┌─────────────┴─────────┐    │
│                                    │ KIS Order Executor     │    │
│                                    │ 모의투자 / 실전투자    │    │
│                                    │ (찬희 최종 승인)       │    │
│                                    └───────────────────────┘    │
│                                                                  │
├──────────────────────────────────────────────────────────────────┤
│ L0: 피드백 루프 (kis_backtest/portfolio/review_engine.py)        │
│                                                                  │
│ ┌─────────────────────────────────────────────────────┐         │
│ │ ReviewEngine.weekly_review()                         │         │
│ │                                                      │         │
│ │  성과 vs KOSPI200        Kill Condition 체크          │         │
│ │  팩터 기여도 분해        비용 실제 vs 모델            │         │
│ │  터뷸런스 추이           DD 한도 접근 경고            │         │
│ │                                                      │         │
│ │  → WeeklyReport                                      │         │
│ │  → Vault 마크다운 저장                               │         │
│ │  → Discord 공유 (HERMES)                             │         │
│ │  → 전략 조정 권고 → L3로 피드백                      │         │
│ └─────────────────────────────────────────────────────┘         │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. 데이터 흐름 (E2E 워크플로우)

```
찬희: "/quant-fund korean-multifactor"
  │
  │ ①──── MCP 데이터 수집 ──────────────────────────────
  │        get_stock_price(005930, 1y)  → 243일 주가
  │        dart_financial_statements()  → CFS 재무
  │        ecos_get_base_rate()         → 기준금리
  ▼
  │ ②──── 팩터 스코어링 ────────────────────────────────
  │        factor_score(momentum + value + low_vol)
  │        → NAVER 1위(0.408) > SK하이닉스 2위(0.250)
  ▼
  │ ③──── 포트폴리오 최적화 ────────────────────────────
  │        portadv_black_litterman(views)
  │        → 삼성전자 -21.8% (숏 신호!)
  ▼
  │ ④──── 개인투자자 제약 ──────────────────────────────
  │        공매도 불가 → 음수 비중 0% 클리핑
  │        → 나머지 4종목 25%씩 재정규화
  ▼
  │ ⑤──── QuantPipeline.run() ──────────────────────────
  │        변동성 타겟팅: SK 25%→2.5% (vol 99%)
  │        거래비용: RT 0.33%, 연 3.96%
  │        DD 체크: 현재 NORMAL
  │        집중도: IT 2.5% < 35% ✓
  │        Kelly: 실제 수익률에서 계산 (하드코딩 X)
  ▼
  │ ⑥──── 리스크 게이트 ────────────────────────────────
  │        [✓] 7항목 전부 PASS
  ▼
  │ ⑦──── PortfolioOrder 생성 ─────────────────────────
  │        NAVER 4.8% | 포스코퓨처엠 3.4% | LG화학 3.2%
  │        SK하이닉스 2.5% | 현금 86.1%
  ▼
  │ ⑧──── KIS 실행 (다음 단계) ────────────────────────
  │        모의투자 4주 → 소액 실전 50만원
  ▼
  │ ⑨──── 주간 복기 ───────────────────────────────────
  │        성과/팩터/비용/Kill Condition → 전략 조정
  └──── → ①로 돌아감 (월간 리밸런싱)
```

---

## 5. 코드 구조

```
open-trading-api/backtester/
│
├── kis_backtest/
│   │
│   ├── core/                          # 핵심 엔진
│   │   ├── pipeline.py                ★ QuantPipeline — E2E 통합
│   │   ├── schema.py                    전략 스키마 (StrategySchema)
│   │   ├── converters.py                YAML/Preset/Dict → Schema 변환
│   │   └── __init__.py
│   │
│   ├── strategies/
│   │   ├── risk/                      # 리스크 관리 모듈
│   │   │   ├── cost_model.py          ★ 한국 거래비용 + After-cost Kelly
│   │   │   ├── drawdown_guard.py      ★ 3단계 DD + 집중도 검증
│   │   │   ├── vol_target.py          ★ EWMA 변동성 타겟팅 + 터뷸런스
│   │   │   ├── position_sizer.py        LEAN 포지션 사이징 코드 생성
│   │   │   └── __init__.py
│   │   ├── preset/                      10개 프리셋 전략
│   │   │   ├── sma_crossover.py
│   │   │   ├── momentum.py
│   │   │   ├── volatility_breakout.py
│   │   │   └── ...
│   │   └── base.py
│   │
│   ├── portfolio/                     # 포트폴리오 관리
│   │   ├── mcp_bridge.py              ★ MCP 분석 → PortfolioOrder 변환
│   │   ├── mcp_connector.py           ★ MCP 결과 정규화 + health check
│   │   ├── review_engine.py           ★ 주간 복기 엔진
│   │   ├── analyzer.py                  상관/분산/효율적 프론티어
│   │   ├── rebalance.py                 리밸런싱 시뮬레이터
│   │   └── visualizer.py               포트폴리오 시각화
│   │
│   ├── codegen/                         LEAN C# 코드 생성기
│   ├── dsl/                             전략 Rule Builder
│   ├── file/                            .kis.yaml 전략 파일 관리
│   ├── lean/                            LEAN 프로젝트 매니저
│   ├── providers/kis/                   KIS API 인증/주문/WebSocket
│   ├── models/                          데이터 모델
│   └── client.py                        BacktestClient 메인
│
├── tests/                             # 테스트 (53/53 PASS)
│   ├── test_risk_modules.py             35 tests (비용/DD/변동성/집중도/브릿지)
│   ├── test_review_engine.py            8 tests (복기/Kill Condition)
│   └── test_e2e_pipeline.py             10 tests (4 시나리오)
│
└── kis_mcp/                             KIS 백테스트 MCP 서버
    └── tools/
        ├── backtest.py
        ├── strategy.py
        └── report.py
```

---

## 6. 핵심 모듈 상세

### 6.1 KoreaTransactionCostModel (cost_model.py)

```
목적: 한국 증권 거래비용의 정확한 모델링.
      Renaissance의 "secret weapon" = 트랜잭션 코스트 모델.

비용 구조 (비대칭):
  매수: 수수료 0.015%                    = 0.015%
  매도: 수수료 0.015% + 세금 0.20%       = 0.215%
  왕복: 0.015% + 0.215%                  = 0.230%
  + 슬리피지 5bps × 2                    = 0.330% (총)

After-cost Kelly:
  f* = fraction × (μ - N×τ - rf) / σ²
  
  예시 (μ=15%, σ=25%, rf=3.5%, 12RT/yr, Half-Kelly):
  → f* = 0.5 × (0.15 - 0.0396 - 0.035) / 0.0625
  → f* = 60.3%
  
  주간(50RT): f* = 0% (비용 > 수익)
  → 한국에서 고빈도 = 수학적 자살
```

### 6.2 DrawdownGuard (drawdown_guard.py)

```
목적: 포트폴리오 드로다운 3단계 경보.
      Millennium: 5%→경고, 7.5%→축소, 10%→종료.

         0%  ──────── NORMAL ────────
        -5%  ──────── WARNING ─────── (신규 매수 중단)
       -7.5% ──────── REDUCE ──────── (전 포지션 50% 축소)
       -10%  ──────── HALT ────────── (전 포지션 청산, 찬희 승인)

+ 집중도 검증:
  종목 ≤ 15%  |  섹터 ≤ 35%  |  상관 ≤ 0.6
```

### 6.3 VolatilityTargeter (vol_target.py)

```
목적: 모든 종목을 동일 리스크 기여도로 정규화.
      Moskowitz (2012): 모멘텀 알파의 진짜 소스 = 변동성 스케일링.

공식: Weight_scaled = raw_weight × (target_vol / estimated_vol)
      단, max_leverage 1.5x 상한.

예시 (목표 10%):
  SK하이닉스: vol 99% → scale 0.101 → 25%→2.5%
  NAVER:      vol 52% → scale 0.192 → 25%→4.8%
  → 고변동 종목 자동 축소, 저변동 종목 자동 확대

+ 터뷸런스 인덱스 (Kritzman & Li 2010):
  Mahalanobis 거리로 현재 시장 스트레스 측정.
  > 5.0이면 위기 경보.
```

### 6.4 QuantPipeline (pipeline.py)

```
목적: 모든 부품을 하나의 호출로 엮는 E2E 파이프라인.

pipeline = QuantPipeline(PipelineConfig(
    total_capital=5_000_000,
    target_vol=0.10,
    kelly_fraction=0.5,        # Half-Kelly
    dd_warning=-0.05,          # Millennium 규칙
    dd_reduce=-0.075,
    dd_halt=-0.10,
))

result = pipeline.run(
    factor_scores={...},       # MCP factor_score 결과
    optimal_weights={...},     # MCP BL/HRP 결과
    returns_dict={...},        # 종목별 일간 수익률
    backtest_sharpe=0.85,
    backtest_max_dd=-0.12,
)

# result.order → PortfolioOrder (종목별 배분 지시서)
# result.risk_passed → True/False
# result.vol_adjustments → 변동성 조정 배수
# result.turb_index → 터뷸런스

report = pipeline.review(equity_curve=[...])
# → WeeklyReport (성과/팩터/비용/Kill Condition/권고)
```

---

## 7. 전략 프리셋

| # | 전략 | 거래세 영향 | Tier | 학술 근거 |
|---|------|-----------|------|---------|
| 1 | 한국 멀티팩터 (월간) | -3.96%/yr | 1 | Asness 2013, Kang 2019 |
| 2 | 펀딩레이트 차익 (크립토) | 거래소 수수료만 | 1 | ScienceDirect 2025 |
| 3 | 시계열 모멘텀 (월간) | -3.96%/yr | 1 | Moskowitz 2012 |
| 4 | 페어트레이딩 (롱-롱) | -5.5%/yr | 2 | Gatev 2006 |
| 5 | 볼 타이밍 (HMM 레짐) | 가변 | 3 | Baum-Welch |
| 6 | ML 멀티알파 | 가변 | 3 | López de Prado 2018 |

**개인투자자 제약**: 공매도 불가 → 인버스 ETF 또는 롱-롱 비중 차이로 대체.

---

## 8. 인프라 연결

```
┌─ VPS (62.171.141.206) ──────────────────────────────────┐
│                                                          │
│  Nexus Finance MCP (:8100)                              │
│  398 도구 / 64 서버                                     │
│  Bearer 토큰 인증                                       │
│                                                          │
│  HERMES (발행, :18789)  ─── Discord/GitHub Pages        │
│  NEXUS  (데이터, :18790) ── MCP 호스팅                  │
│                                                          │
│  Obsidian Vault (1,308 노트, PARA 구조)                 │
│                                                          │
├─ WSL ────────────────────────────────────────────────────┤
│  DOGE (리서치, :18794) ── 딥 리서치/퀀트 검증           │
│                                                          │
├─ 로컬 (Windows 11) ─────────────────────────────────────┤
│                                                          │
│  Claude Code                                             │
│    ├─ .mcp.json (nexus-finance + kis-backtest + drawio) │
│    ├─ skills/quant-fund/  (SKILL.md v2.1)               │
│    ├─ commands/quant-fund.md                             │
│    └─ hooks/ (3개)                                      │
│         ├─ session_start.py    세션 시작 환경 점검       │
│         ├─ validate_report.py  보고서 품질 자동 검증     │
│         └─ validate_quant.py   퀀트 코드 pytest 자동     │
│                                                          │
│  KIS Backtest MCP (:3846)                               │
│  KIS API (~/KIS/config/kis_devlp.yaml)                  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 9. 피드백 루프

```
                    ┌─────────────┐
                    │ 실행 결과   │
                    │ (체결/수익) │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ 성과     │ │ 비용     │ │ Kill     │
        │ 분석     │ │ 분석     │ │ Condition│
        │          │ │          │ │          │
        │ 수익 vs  │ │ 실제 vs  │ │ CUFA IP  │
        │ 벤치마크 │ │ 모델     │ │ 반증조건 │
        └────┬─────┘ └────┬─────┘ └────┬─────┘
             │            │            │
             └────────────┼────────────┘
                          │
                    ┌─────┴─────┐
                    │ 전략 조정 │
                    │           │
                    │ 팩터 비중 │ ───→ 다음 리밸런싱
                    │ 종목 교체 │
                    │ DD 대응   │
                    └───────────┘
```

---

## 10. 검증 현황

### 테스트 (53/53 PASS)

| 파일 | 테스트 수 | 대상 |
|------|----------|------|
| test_risk_modules.py | 35 | 비용 모델, DD 가드, 변동성, 집중도, 브릿지 |
| test_review_engine.py | 8 | 복기 엔진, Kill Condition, 마크다운 |
| test_e2e_pipeline.py | 10 | 4 시나리오 (정상/섹터초과/DD발생/Kill발동) |

### 실제 데이터 E2E (2026.04.04)

| 단계 | 결과 |
|------|------|
| KRX 데이터 수집 | 5종목 243일 ✓ |
| 팩터 스코어링 | NAVER 1위, SK하이닉스 2위 ✓ |
| Black-Litterman | Sharpe 0.69 ✓ |
| 공매도 제약 | 삼성전자 -21.8% → 0% 클리핑 ✓ |
| 변동성 타겟팅 | SK 25%→2.5%, NAVER 25%→4.8% ✓ |
| 리스크 게이트 | ALL PASS ✓ |
| 복기 | 팩터 기여도 + Kill Condition ✓ |

### Hooks (3개)

| Hook | 트리거 | 역할 |
|------|--------|------|
| session_start.py | SessionStart | MCP/테스트/Git/디스크 점검 |
| validate_report.py | PostToolUse(Write\|Edit) | 보고서 품질 검증 |
| validate_quant.py | PostToolUse(Write\|Edit) | 퀀트 pytest 자동 |

---

## 11. 빠른 시작

```bash
# 1. 의존성
cd ~/Desktop/open-trading-api/backtester
pip install numpy pandas scipy pykrx

# 2. 테스트 실행
python -m pytest tests/ -v

# 3. 파이프라인 실행 (Python)
from kis_backtest.core.pipeline import QuantPipeline, PipelineConfig

pipeline = QuantPipeline(PipelineConfig(total_capital=5_000_000))
result = pipeline.run(
    factor_scores={"005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"}},
    optimal_weights={"005930": 0.10},
    backtest_sharpe=0.8,
    backtest_max_dd=-0.12,
)
print(result.order.summary())

# 4. Claude Code에서
/quant-fund korean-multifactor
```

---

## 12. 다음 마일스톤

| 단계 | 작업 | 상태 |
|------|------|------|
| v0.1α | 리스크 모듈 + 파이프라인 + 테스트 53개 | ✅ 완료 |
| v0.1β | MCP 실연동 + 현대건설 E2E + 134 tests | ✅ 완료 |
| v0.2α | UniverseBuilder + StrategyComparison + BL/HRP + 179 tests | ✅ 완료 |
| v0.2 | KIS 모의투자 실 주문 + 첫 복기 | 다음 |
| v0.3 | CUFA 보고서 ↔ 퀀트 Kill Condition 자동 연동 | 예정 |
| v0.4 | 크립토 펀딩레이트 차익 전략 추가 | 예정 |
| v1.0 | 4주 모의투자 검증 완료 → 소액 실전 진입 | 목표 |

### v0.2α 변경사항

**Bug Fix:**
- `get_returns_dict()`: period 파라미터 버그 수정 → start_date/end_date + 병렬 Semaphore
- `get_bl_weights()`: MCP 필수 파라미터(series_list, names) 추가 + 시그니처 개선

**신규 모듈:**
- `UniverseBuilder` (portfolio/universe_builder.py): 6섹터 자동 종목 선별
  - stocks_search MCP → DART 재무비율 스크리닝 → ROE/OPM/DTE 점수
- `StrategyComparison` (core/strategy_comparison.py): 멀티 전략 비교 러너
  - N개 전략 동일 유니버스 백테스트 → Sharpe/MDD 랭킹
  - BL/HRP 포트폴리오 최적화 비교 + 자동 추천

**신규 MCP 래퍼:**
- `get_hrp_weights()`: portadv_hrp MCP 도구 (López de Prado HRP)
- `search_stocks()`: stocks_search MCP 도구 (한국어 종목 검색)

### v0.3α 변경사항 (2026-04-08)

**신규 모듈 (Phase 1-4+6 실행 계층):**

- `walk_forward.py` (core/walk_forward.py): Walk-Forward OOS 검증
  - N-fold 롤링/확장 윈도우 IS→OOS 분석
  - Sharpe degradation 추적 (과최적화 탐지)
  - 멀티 종목 포트폴리오 검증 (validate_multi_asset)
  - 21 tests

- `capital_ladder.py` (execution/capital_ladder.py): 점진적 자본 배포
  - 5단계: PAPER(0%) → SEED(10%) → GROWTH(30%) → SCALE(60%) → FULL(100%)
  - 자동 승격/강등 (Sharpe/MDD/기간 기반)
  - JSON 상태 영속성 (서버 재시작 시 복원)
  - 40 tests

- `upbit/` (providers/upbit/): 업비트 거래소 클라이언트
  - REST: 시세, 호가, 캔들, 계좌, 주문 (JWT 인증)
  - WebSocket: ticker/trade/orderbook 실시간 스트리밍
  - pyupbit(Apache 2.0) 참고, httpx/websockets 기반 자체 구현
  - 31 tests

**인프라:**
- `conftest.py`: 루트에서 pytest 실행 가능 (sys.path 자동 추가)
- `setup_scheduler.ps1`: Windows Task Scheduler 자동 등록 (일일 16:00/주간 금 16:30)
- `run_paper_trading.py --ladder`: Capital Ladder 연동 옵션 추가

**테스트:** 286 → 378 (+92), 회귀 0건

---

---

## 13. v0.4α 신규 — Luxon Terminal & 선순환 아키텍처

> Sprint 7+ 에서 추가된 7계층 통합 + 3개 피드백 루프 완성

### 13.1 전체 7계층 아키텍처 (v0.4α)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Layer 7: 진입점 & 스크립트                                             │
│  ├── scripts/luxon_server.py   (Phosphor Terminal 대시보드 :7777)        │
│  ├── scripts/luxon_run.py      (CLI 사이클 러너)                         │
│  └── scripts/luxon_backtest_runner.py (백테스트 배치)                   │
├─────────────────────────────────────────────────────────────────────────┤
│  Layer 6: LuxonTerminal (통합 파사드)                                   │
│  └── kis_backtest/luxon/terminal.py   (11단계 사이클)                   │
├─────────────────────────────────────────────────────────────────────────┤
│  Layer 5: 오케스트레이션 & 피드백                                        │
│  ├── luxon/orchestrator.py            (LuxonOrchestrator)                │
│  ├── portfolio/feedback_adapter.py    (BREAK1/2 해결)                   │
│  └── luxon/graph/ingestors/signal_accuracy_tracker.py  (BREAK3 해결)   │
├─────────────────────────────────────────────────────────────────────────┤
│  Layer 4: 포트폴리오 관리                                               │
│  ├── portfolio/catalyst_tracker.py                                      │
│  ├── portfolio/kill_switch.py                                           │
│  ├── portfolio/capital_ladder.py                                        │
│  └── portfolio/weekly_review.py                                         │
├─────────────────────────────────────────────────────────────────────────┤
│  Layer 3: 지식 그래프 & TA 신호                                         │
│  ├── luxon/graph/graph.py             (GothamGraph)                     │
│  ├── luxon/graph/nodes.py / edges.py                                    │
│  └── luxon/graph/ingestors/                                             │
│       ├── ta_signal_ingestor.py       (RSI/MACD/BB → EventNode)        │
│       ├── macro_ingestor.py                                             │
│       └── news_ingestor.py                                              │
├─────────────────────────────────────────────────────────────────────────┤
│  Layer 2: 리스크 & 백테스트 (기존 v0.3α)                               │
│  ├── risk/ 6모듈                                                        │
│  └── core/pipeline.py (QuantPipeline)                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  Layer 1: MCP 브릿지 & 데이터                                           │
│  ├── mcp/mcp_bridge.py        (로컬:8100 → VPS 폴백)                   │
│  └── mcp/macro_regime.py      (레짐 감지)                              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 13.2 MCP TA 2단계 파이프라인 (핵심 발견)

nexus-finance-mcp `ta_*` 도구는 **symbol이 아닌 OHLCV 배열**을 받는 순수 함수.

```
종목코드
   │
   ▼
stocks_history(stock_code, limit=40)
   │ → {"success": True, "data": [{date, open, high, low, close, volume}, ...]}
   ▼
ohlcv: list[dict]  (40행)
   │
   ├──→ ta_rsi(data=ohlcv, period=14)
   │       응답: {"success": True, "latest_value": 39.24, "signal": "neutral"}
   │
   ├──→ ta_macd(data=ohlcv)
   │       응답: {"success": True, "latest": {"macd": 3149.4, "signal": None}}
   │
   └──→ ta_bollinger(data=ohlcv)
           응답: {"success": True, "latest": {"upper": 215313, "lower": 168326, "close": 201500}}
```

### 13.3 선순환 피드백 루프 (3개 BREAK → 해결)

```
┌─────────────────────────────────────────────────────────────────────┐
│                      선순환 피드백 (v0.4α 완성)                      │
│                                                                     │
│   ~/.luxon/convictions.json                                         │
│          │ 로드                                                      │
│          ▼                                                          │
│   LuxonOrchestrator.run_workflow()                                  │
│          │                                                          │
│          ▼                                                          │
│   KIS 주문 실행 (paper_mode=True)                                   │
│          │                                                          │
│          ▼                                                          │
│   포트폴리오 성과 측정                                              │
│     │              │                                                │
│     ▼              ▼                                                │
│  WeeklyReport   SignalAccuracyTracker.update_outcomes()             │
│     │              │                                                │
│     ▼              ▼                                                │
│  FeedbackAdapter  학습된 확률 저장                                  │
│  .apply()         ~/.luxon/signal_accuracy.json                     │
│     │                                                               │
│     ├── BREAK1 해결: action_items → conviction 자동 조정 ✅         │
│     ├── BREAK2 해결: kill_conditions → KillSwitch.activate() ✅     │
│     └── BREAK3 해결: TA probability 0.3×기본+0.7×학습 ✅           │
│          │                                                          │
│          ▼                                                          │
│   ~/.luxon/convictions.json 저장                                    │
│          │                                                          │
│          └──────────────── 다음 사이클에 로드 ─────────────────────┘
└─────────────────────────────────────────────────────────────────────┘
```

### 13.4 LuxonTerminal 11단계 사이클

```python
from kis_backtest.luxon import LuxonTerminal, TerminalConfig

terminal = LuxonTerminal(TerminalConfig(
    symbols=["005930", "000660", "035720"],
    initial_capital=10_000_000,
    paper_mode=True,
    cycle_interval_minutes=60,
))
terminal.boot()
report = terminal.cycle()   # 11단계 자동 실행
terminal.run_loop(max_cycles=24)
```

사이클 11단계:
1. KillSwitch 체크 → active면 즉시 반환
2. MacroRegime 감지 (ecos + fred 다수결)
3. SignalAccuracyTracker 학습 데이터 로드
4. FeedbackAdapter.load_persisted_convictions()
5. TASignalIngestor.ingest() → GothamGraph 주입
6. LuxonOrchestrator.run_workflow()
7. 체결 추출 + Paper 기록
8. WeeklyReviewEngine.generate()
9. FeedbackAdapter.apply() (컨빅션 조정 + 킬스위치 발동)
10. FeedbackAdapter.save_convictions()
11. SignalAccuracyTracker.save()

### 13.5 Phosphor Terminal 대시보드

```
scripts/luxon_server.py
  ├── HTTP :7777
  │   ├── GET /           Phosphor HTML (VT323폰트, 앰버 #d4a017, CRT 스캔라인)
  │   │                   JS setInterval(30초) → /api/data 폴링
  │   └── GET /api/data   실시간 JSON
  │                       {regime, portfolio, ta_signals, pnl_curve, fills,
  │                        capital_stage, kill_switch}
  │
  └── DataService (백그라운드 스레드)
      ├── 30초마다 MCPDataProvider._fetch_all()
      ├── _fetch_pnl(): get_stock_returns_sync() → 누적 실 PnL
      └── _fetch_ta_signals(): TASignalIngestor.ingest_sync()
```

### 13.6 퍼시스턴스 레이어

```
~/.luxon/
├── convictions.json       {symbol: score, ...}  ← FeedbackAdapter 관리
├── signal_accuracy.json   {RSI: {hits, total, ...}, ...}  ← SignalAccuracyTracker
├── capital_ladder.json    {stage: "PAPER", ...}
├── kill_switch.json       {active: false, activated_at: null}
└── paper_fills.json       [{symbol, qty, price, date}, ...]
```

### 13.7 품질 게이트

```python
# tests/test_no_hardcoded_vps_ip_in_logic.py
def test_no_hardcoded_vps_ip_in_logic():
    """logic 파일에 VPS IP(62.171.x.x) 하드코딩 금지.
    반드시 os.environ.get('MCP_VPS_HOST', '') 사용."""
```

`terminal.py`에서:
```python
# ✓ 올바른 방법
vps_host = os.environ.get("MCP_VPS_HOST", "")
```

### 13.8 v0.4α 테스트 추가

```
tests/luxon/
├── test_ta_signal_ingestor.py   (2단계 파이프라인, FakeMCPTA with stocks_history)
├── test_feedback_adapter.py     (BREAK1/2)
├── test_signal_accuracy.py      (BREAK3 학습)
└── test_terminal.py             (11단계 사이클 E2E)
```

### 13.9 v0.4α 마일스톤 달성

| 항목 | 상태 |
|------|------|
| LuxonTerminal 파사드 (7계층 통합) | ✅ |
| TA 2단계 파이프라인 (stocks_history→ta_*) | ✅ |
| BREAK1: WeeklyReport→컨빅션 자동갱신 | ✅ |
| BREAK2: KillCondition→KillSwitch 자동발동 | ✅ |
| BREAK3: TA 확률 학습 (SignalAccuracyTracker) | ✅ |
| Phosphor Terminal 대시보드 (:7777) | ✅ |
| VPS IP 하드코딩 금지 품질 게이트 PASS | ✅ |
| 907+ tests (회귀 0건) | ✅ |

---

*Luxon Terminal v1.0 — MIT License*
