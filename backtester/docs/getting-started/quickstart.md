# Quickstart

## 5-minute paper trade

```bash
pip install luxon-terminal
cp .env.example .env
luxon-run --max-cycles 1
```

That's it. Reads `~/KIS/config/kis_devlp.yaml` (or Alpaca env vars),
runs one complete cycle, writes paper fills to `fills/paper/*.json`.

## Programmatic usage

```python
from kis_backtest.luxon import LuxonTerminal, TerminalConfig

config = TerminalConfig(
    symbols=["005930", "000660", "035420"],
    capital=10_000_000,
    paper_mode=True,
)
terminal = LuxonTerminal(config)
terminal.boot()

report = terminal.cycle()
print(report.summary())

# Run continuously with stage-aware intervals
terminal.run_loop(max_cycles=10, stage_aware_interval=True)
```

## With CUFA conviction injection

```python
from pathlib import Path

config = TerminalConfig(
    symbols=["005930", "000660"],
    paper_mode=True,
    cufa_digests_dir=Path("~/cufa_reports").expanduser(),
)
```

On boot, Luxon scans the directory for `*.json` and `*.html` digests,
computes conviction per symbol using the formula:

```
conviction = clamp( 5.0 + min(IP_count, 4) - triggered_kills * 2, 1, 10 )
```

and writes to `~/.luxon/convictions.json` — picked up by next cycle.

## Walk-Forward OOS + auto-promote

```bash
luxon-wf \
    --equity-file data/equity.json \
    --n-folds 5 \
    --auto-promote \
    --ladder-state data/ladder.json
```

If OOS Sharpe ≥ 0.5, MaxDD > -10%, win_rate ≥ 0.4, and days_in_stage ≥ 20,
`CapitalLadder` promotes **PAPER → SEED** (10% capital).

## Next steps

- [Configuration](configuration.md) — env vars and config options
- [Paper Trading guide](../guides/paper-trading.md) — 4-week workflow
- [Multi-Broker](../guides/brokers.md) — switching from KIS to Alpaca
