# Luxon Terminal

> **AI 기반 퀀트 운용 시스템** — 6-exchange 멀티 브로커 + Walk-Forward OOS + 자동 자본 승급 + 선순환 피드백

[![CI](https://github.com/pollmap/luxon-terminal/actions/workflows/ci.yml/badge.svg)](https://github.com/pollmap/luxon-terminal/actions/workflows/ci.yml)
[![Security](https://github.com/pollmap/luxon-terminal/actions/workflows/security.yml/badge.svg)](https://github.com/pollmap/luxon-terminal/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](backtester/LICENSE)
[![Tests](https://img.shields.io/badge/tests-989%20passed-brightgreen.svg)](backtester/tests/)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Luxon Terminal은 **데이터 수집 → 분석 → 주문 실행 → 복기 → 자동 개선**까지 전체 퀀트 루프를 자동화하는 오픈소스 프레임워크입니다.

---

## ✨ 한눈에 보기

```
┌─────────────────────────────────────────────────────────────┐
│  Data → Analysis → Orchestration → Execution → Feedback      │
│  (KIS/Alpaca/MCP)  (Macro/TA)  (Ackman-Druckenmiller)        │
│         ↓              ↓            ↓           ↓            │
│                RiskGateway (9 gates)                         │
│                     ↓                                         │
│        Capital Ladder: PAPER→SEED→GROWTH→SCALE→FULL          │
│                     ↓                                         │
│        Walk-Forward OOS → 자동 승급                            │
└─────────────────────────────────────────────────────────────┘
```

**선순환 3루프**: Weekly → Δconviction · Kill condition → Switch · TA → Probability

---

## 🚀 설치

```bash
# 1. pip 한 줄 설치 (PyPI 없이 GitHub 직접)
pip install "git+https://github.com/pollmap/luxon-terminal.git@v1.2.0#subdirectory=backtester"

# 2. 모든 브로커 + 대시보드 포함
pip install "git+https://github.com/pollmap/luxon-terminal.git@v1.2.0#subdirectory=backtester[all]"

# 3. 원본 클론 (Docker 권장)
git clone https://github.com/pollmap/luxon-terminal.git
cd luxon-terminal/backtester
cp .env.example .env
docker compose up -d       # 컨테이너 2개 + 대시보드 :7777
```

## ⚡ 5분 퀵스타트

```bash
# 페이퍼 모드 1회 사이클 (가장 안전)
python -m kis_backtest.luxon.terminal_cli --max-cycles 1

# CUFA 보고서 자동 주입
python -m kis_backtest.luxon.terminal_cli --cufa-digests ~/cufa_digests --max-cycles 1

# Walk-Forward 검증 + CapitalLadder 자동 승급
python -m kis_backtest.luxon.wf_cli \
    --equity-file data/equity.json \
    --auto-promote --ladder-state data/ladder.json

# 라이브 (페이퍼 API, 실계좌 X)
python -m kis_backtest.luxon.terminal_cli --live --max-cycles 1
```

## 💻 Python API

```python
from kis_backtest.luxon import LuxonTerminal, TerminalConfig

config = TerminalConfig(
    symbols=["005930", "000660", "035420"],   # 삼성전자/SK하이닉스/NAVER
    capital=10_000_000,                        # 1천만원
    paper_mode=True,
)
terminal = LuxonTerminal(config)
terminal.boot()

report = terminal.cycle()
print(report.summary())

# 자동 루프 (stage-aware: PAPER=1h, SEED=4h, GROWTH+=1d)
terminal.run_loop(max_cycles=None, stage_aware_interval=True)
```

---

## 🌐 지원 브로커

| 거래소 | 국가 | 자산 | Paper | Live | Extras |
|---|---|---|:---:|:---:|---|
| **KIS** | 한국 | 주식 | ✅ | ✅ | core |
| **Alpaca** | 미국 | 주식 | ✅ | ✅ | `[alpaca]` |
| **IBKR** | 글로벌 | 주식/선물 | ✅ | ✅ | `[ibkr]` |
| **Upbit** | 한국 | 암호화폐 | ✅ | ✅ | core |
| **Crypto.com** | 글로벌 | 암호화폐 | ✅ | ✅ | `[crypto]` |

모든 브로커는 동일한 `BrokerageProvider` / `PriceProvider` Protocol을 구현해서 `LiveOrderExecutor`에 바로 주입할 수 있습니다.

---

## 🏗 아키텍처 (7 계층)

```
┌────────────────────────────────────────────────────┐
│ L7  Feedback       FeedbackAdapter (선순환 종점)      │
│ L6  Observability  Phosphor Dashboard :7777          │
│ L5  Intelligence   MCP bridge + LLM router (선택)     │
│ L4  Execution      OrderExecutor + RiskGateway(9)    │
│ L3  Orchestration  Ackman-Druckenmiller + CUFA       │
│ L2  GothamGraph    SYMBOL/SECTOR/PERSON/THEME        │
│ L1  Analysis       Macro regime (10 indicators) + TA │
│ L0  Data           KIS / Alpaca / IBKR / MCP / FRED  │
└────────────────────────────────────────────────────┘
```

### 핵심 공식

**Conviction (CUFA → 1-10)**
```
conviction = clamp( 5.0 + min(IP_count, 4) × 1.0 − triggered_kills × 2.0, 1, 10 )
```

**Position Size (Half-Kelly + Regime)**
```
weight_final = min(conviction/100, max_pct) × regime_multiplier
  where regime_multiplier ∈ {EXPANSION:1.0, RECOVERY:0.8, CONTRACTION:0, CRISIS:0+SELL}
```

**PAPER → SEED 승급 조건**
```
can_promote = (days ≥ 20) ∧ (OOS_Sharpe ≥ 0.5) ∧ (OOS_MaxDD > −10%) ∧ (win_rate ≥ 0.4)
```

자세한 내용은 [`backtester/ARCHITECTURE.md`](backtester/ARCHITECTURE.md) 참조.

---

## 🛡 9-Gate 리스크 제어

| Gate | 체크 항목 | 단계 |
|:---:|---|---|
| 1 | KillSwitch 비활성 | 모든 단계 |
| 2 | Pipeline risk_passed | 모든 단계 |
| 3 | DD 상태 ≠ HALT | 모든 단계 |
| 4 | 시장 시간 (09:00–15:30 KST / 09:30–16:00 ET) | 실주문 |
| 5 | 총 매수 ≤ available_cash | 모든 단계 |
| 6 | 단일 주문 ≤ 30% of cash | 모든 단계 |
| 7 | Rate limit (분당 10건) | 모든 단계 |
| 8 | 종목 비중 ≤ 5% of equity | SEED+ |
| 9 | 섹터 비중 ≤ 20% of equity | SEED+ |

---

## 📊 5단계 자본 승급

```
 [PAPER] 0%  ─ 20일, Sharpe≥0.5, DD>-10% ─┐
                                          ▼
                                      [SEED] 10%  (종목≤5%, 섹터≤20%)
                                          │
                                          ▼
                                      [GROWTH] 30%
                                          │
                                          ▼
                                      [SCALE] 60%
                                          │
                                          ▼
                                      [FULL] 100%
 ◀── demote (DD×1.5 초과 시 자동 강등) ──┘
```

---

## 📚 문서

- **📖 문서 사이트**: https://pollmap.github.io/luxon-terminal/
- [ARCHITECTURE.md](backtester/ARCHITECTURE.md) — 7-layer 상세 설계
- [SECURITY.md](backtester/SECURITY.md) — 보안 정책 + 취약점 제보
- [CONTRIBUTING.md](backtester/CONTRIBUTING.md) — 기여 가이드
- [CHANGELOG.md](backtester/CHANGELOG.md) — 버전 이력
- [CODE_OF_CONDUCT.md](backtester/CODE_OF_CONDUCT.md)

---

## 🔧 개발

```bash
git clone https://github.com/pollmap/luxon-terminal.git
cd luxon-terminal/backtester
pip install -e ".[dev]"

pytest tests/ -q                  # 989 tests
ruff check kis_backtest/
mypy kis_backtest/luxon/
bandit -r kis_backtest/
```

CI/CD:
- `ci.yml` — pytest (3.11/3.12) + ruff + mypy + codecov
- `security.yml` — bandit + pip-audit + gitleaks (주간)
- `publish.yml` — release → PyPI (OIDC trusted publishing)
- `docs.yml` — mkdocs → GitHub Pages

---

## 🆚 비교

| 기능 | Luxon | zipline | backtrader | QuantConnect |
|---|:---:|:---:|:---:|:---:|
| Walk-Forward OOS | ✅ | ➖ | ➖ | ✅ |
| 자본 승급 Ladder | ✅ | ❌ | ❌ | ❌ |
| 멀티 브로커 라이브 | ✅ (5) | ❌ | ✅ | ✅ |
| CUFA 펀더멘털 브릿지 | ✅ | ❌ | ❌ | ➖ |
| MCP 통합 | ✅ | ❌ | ❌ | ❌ |
| 선순환 피드백 | ✅ (3) | ❌ | ❌ | ➖ |
| i18n 프롬프트 | ✅ | ❌ | ❌ | ❌ |
| MIT License | ✅ | ✅ | ✅ | ❌ |

---

## 🤝 기여

기여 환영합니다. PR 전 체크리스트는 [CONTRIBUTING.md](backtester/CONTRIBUTING.md) 참조.

- 🐛 버그 리포트: [Issues](https://github.com/pollmap/luxon-terminal/issues/new?template=bug_report.yml)
- ✨ 기능 제안: [Issues](https://github.com/pollmap/luxon-terminal/issues/new?template=feature_request.yml)
- 🔒 보안 취약점: [SECURITY.md](backtester/SECURITY.md)

---

## 📜 라이선스

[MIT](backtester/LICENSE) — 금융 소프트웨어 면책 조항 포함.

> ⚠️ **본 소프트웨어는 연구/실험용이며 투자 자문이 아닙니다.**
> 모의투자로 충분히 검증한 후 실전 API에 연결하세요.
> 거래 손실에 대한 책임은 사용자에게 있습니다.

---

## 🔗 관련 프로젝트

- 이 저장소는 [koreainvestment/open-trading-api](https://github.com/koreainvestment/open-trading-api)의 KIS API 샘플 코드를 포크하여 시작했으며, `backtester/` 디렉토리의 Luxon Terminal 엔진이 핵심 프로덕트입니다. 레거시 KIS 샘플은 [`examples_llm/`](examples_llm/), [`examples_user/`](examples_user/), [`legacy/`](legacy/)에 보존되어 있습니다.

---

<sub>Luxon Terminal v1.2.0 — 989 tests · 6 brokers · MIT License</sub>
