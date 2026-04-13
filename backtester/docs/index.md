# Luxon Terminal

**AI-driven quant trading terminal** with Walk-Forward OOS validation,
Capital Ladder-based position graduation, and multi-broker support (KIS, Alpaca, IBKR coming).

[![CI](https://github.com/YOUR_ORG/luxon-terminal/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_ORG/luxon-terminal/actions/workflows/ci.yml)
[![Security](https://github.com/YOUR_ORG/luxon-terminal/actions/workflows/security.yml/badge.svg)](https://github.com/YOUR_ORG/luxon-terminal/actions/workflows/security.yml)
[![PyPI](https://img.shields.io/pypi/v/luxon-terminal.svg)](https://pypi.org/project/luxon-terminal/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## What is Luxon Terminal?

A research-grade Python framework that automates the **full quant loop**:

```
 Data (KIS/Alpaca/MCP) → Analysis (macro regime, TA)
    → Orchestration (Ackman-Druckenmiller)
    → Execution (order executor + 9-gate risk control)
    → Capital Ladder (PAPER→SEED→GROWTH→SCALE→FULL)
    → Feedback (Walk-Forward OOS → auto-promote)
```

Built around three **virtuous feedback loops**:

1. **Weekly review → conviction adjustment** (BREAK1)
2. **Kill condition → kill switch** (BREAK2)
3. **TA signal accuracy → probability learning** (BREAK3)

## Why?

Most quant libraries stop at backtesting. Luxon Terminal adds:

- **Capital graduation**: Never jumps straight to full capital. Walk-Forward
  OOS ≥ 0.5 Sharpe for 4 weeks → 10% → 30% → 60% → 100%.
- **9-gate risk**: KillSwitch, pipeline risk, DD halt, market hours, cash
  limit, single-order cap, rate limit, **symbol 5%**, **sector 20%**.
- **CUFA bridge**: Fundamental research digest (IP + kill conditions) →
  auto-computed conviction score → feedback adapter.
- **Multi-broker**: KIS (Korea Investment), Alpaca (US), IBKR (v1.2).

## Quickstart

```bash
pip install luxon-terminal
cp .env.example .env  # fill in keys

luxon-run --max-cycles 1                 # one cycle, paper mode
luxon-run --live --cufa-digests ~/cufa   # live with CUFA injection
luxon-wf --equity-file equity.json --auto-promote
```

See [Installation](getting-started/installation.md) and
[Quickstart](getting-started/quickstart.md) for details.

## Status

| Milestone | State |
|---|---|
| v1.0 GA | ✅ 960 tests, MIT license |
| PyPI | 🚧 Publishing via GitHub Actions |
| Alpaca | ✅ v1.1 (paper + live) |
| IBKR | 🚧 v1.2 |
| Docs site | ✅ mkdocs + Material theme |

## License

MIT — see [LICENSE](https://github.com/YOUR_ORG/luxon-terminal/blob/main/backtester/LICENSE).

**Financial software disclaimer**: This project is research tooling, not
investment advice. Always validate against your broker's specs before live
trading.
