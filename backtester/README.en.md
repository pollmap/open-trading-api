# Luxon Terminal

> AI-driven quant trading terminal with Walk-Forward OOS validation,
> Capital Ladder graduation, and multi-broker support.

[![CI](https://github.com/YOUR_ORG/luxon-terminal/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_ORG/luxon-terminal/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/luxon-terminal.svg)](https://pypi.org/project/luxon-terminal/)
[![Python](https://img.shields.io/pypi/pyversions/luxon-terminal.svg)](https://pypi.org/project/luxon-terminal/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- 🧠 **Full quant loop**: data → analysis → orchestration → execution → feedback
- 📈 **Walk-Forward OOS**: 4-fold rolling validation with auto-promotion
- 🪜 **Capital Ladder**: PAPER → SEED(10%) → GROWTH(30%) → SCALE(60%) → FULL(100%)
- 🛡 **9-gate risk control**: KillSwitch, DD halt, market hours, symbol ≤5%,
  sector ≤20%, rate limits
- 🌐 **Multi-broker**: KIS (Korea), Alpaca (US), IBKR (v1.2 planned)
- 🤖 **CUFA bridge**: fundamental digests → auto-computed conviction
- 🔁 **3 feedback loops**: Weekly → conviction, Kill cond → switch, TA → probability
- 🧪 **960+ tests**, MIT license

## Install

```bash
pip install luxon-terminal            # core
pip install "luxon-terminal[all]"     # + exchange, viz, mcp extras
```

## Quickstart (30 seconds)

```bash
cp .env.example .env
luxon-run --max-cycles 1              # paper mode, 1 cycle
luxon-run --live                      # paper broker API
luxon-wf --equity-file equity.json --auto-promote
```

## Programmatic usage

```python
from kis_backtest.luxon import LuxonTerminal, TerminalConfig

terminal = LuxonTerminal(TerminalConfig(
    symbols=["AAPL", "MSFT", "GOOGL"],
    capital=10_000,
    paper_mode=True,
))
terminal.boot()
report = terminal.cycle()
print(report.summary())
```

## Architecture (7 layers)

```
┌──────────────────────────────────────────────────┐
│ L7  Feedback       FeedbackAdapter               │
│ L6  Observability  Phosphor Dashboard :7777      │
│ L5  Intelligence   MCP bridge + LLM router       │
│ L4  Execution      OrderExecutor + RiskGateway   │
│ L3  Orchestration  Ackman-Druckenmiller          │
│ L2  GothamGraph    SYMBOL/SECTOR/PERSON/THEME    │
│ L1  Analysis       Macro regime + TA             │
│ L0  Data           KIS / Alpaca / MCP / FRED     │
└──────────────────────────────────────────────────┘
```

## Virtuous feedback loops

```
BREAK1   WeeklyReport  →  Δconviction  →  next cycle position weight
BREAK2   KillCondition →  KillSwitch    →  next cycle step 1 halt
BREAK3   TA signal     →  accuracy log  →  conviction probability
```

## Documentation

- **Full docs**: https://YOUR_ORG.github.io/luxon-terminal/
- [Architecture](ARCHITECTURE.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Comparison

| Feature                | Luxon Terminal | zipline | backtrader | QuantConnect |
|---|:---:|:---:|:---:|:---:|
| Walk-Forward OOS       | ✅            | ➖      | ➖         | ✅            |
| Capital graduation     | ✅            | ❌      | ❌         | ❌            |
| Multi-broker live      | ✅ (2)        | ❌      | ✅         | ✅            |
| Fundamental bridge     | ✅ (CUFA)     | ❌      | ❌         | ➖            |
| MCP integration        | ✅            | ❌      | ❌         | ❌            |
| MIT license            | ✅            | ✅      | ✅         | ❌            |

## Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) — with financial software disclaimer. **Not investment advice.**
