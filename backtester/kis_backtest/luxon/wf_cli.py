"""`luxon-wf` CLI — Walk-Forward OOS 검증 + CapitalLadder 자동 승급.

설치 후:
    pip install luxon-terminal
    luxon-wf --equity-file data/equity.json --auto-promote \\
             --ladder-state data/ladder.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="luxon-wf",
        description="Walk-Forward OOS validator with CapitalLadder promotion",
    )
    p.add_argument("--equity-file", required=True,
                   help="Equity curve JSON file")
    p.add_argument("--n-folds", type=int, default=5)
    p.add_argument("--train-ratio", type=float, default=0.7)
    p.add_argument("--min-oos-sharpe", type=float, default=0.5)
    p.add_argument("--max-oos-dd", type=float, default=-0.10)
    p.add_argument("--ladder-state", help="CapitalLadder state file (optional)")
    p.add_argument("--total-capital", type=float, default=10_000_000)
    p.add_argument("--auto-promote", action="store_true",
                   help="Auto-promote on WF pass")
    p.add_argument("--output", help="Result JSON output path")
    p.add_argument("--version", action="store_true")
    return p.parse_args()


def _load_returns(path: str | Path) -> list[float]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "returns" in data:
        return [float(r) for r in data["returns"]]
    if not isinstance(data, list):
        raise ValueError(f"Unsupported format: {type(data).__name__}")
    if data and "daily_return" in data[0]:
        return [float(r["daily_return"]) for r in data[1:]]
    equities = [float(r["equity"]) for r in data]
    return [
        (equities[i] - equities[i-1]) / equities[i-1]
        for i in range(1, len(equities))
        if equities[i-1] > 0
    ]


def main() -> int:
    args = _parse_args()

    if args.version:
        from kis_backtest import __version__
        print(f"luxon-wf {__version__}")
        return 0

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    from kis_backtest.core.walk_forward import WalkForwardValidator, WFConfig

    returns = _load_returns(args.equity_file)
    print(f"[1/3] equity loaded: {len(returns)} daily returns")

    validator = WalkForwardValidator(config=WFConfig(
        n_folds=args.n_folds,
        train_ratio=args.train_ratio,
        min_sharpe=args.min_oos_sharpe,
        max_oos_dd=args.max_oos_dd,
    ))
    wf = validator.validate(returns=returns, strategy_fn=lambda r: list(r))

    print(f"\n[2/3] WF verdict: {wf.verdict}")
    print(f"  OOS mean Sharpe: {wf.oos_mean_sharpe:.3f}")
    print(f"  OOS worst DD:    {wf.oos_worst_dd:.1%}")
    print(f"  Win rate:        {wf.win_rate:.0%}")

    promote_msg: Optional[str] = None
    ladder_status: Optional[dict] = None
    if args.ladder_state:
        from kis_backtest.execution.capital_ladder import CapitalLadder, LadderConfig
        ladder = CapitalLadder(LadderConfig(
            total_capital=args.total_capital,
            state_file=args.ladder_state,
        ))
        print(f"\n[3/3] Ladder: {ladder.current_stage.name}")
        if args.auto_promote:
            promote_msg = ladder.promote_if_wf_passed(
                wf, min_oos_sharpe=args.min_oos_sharpe,
                max_oos_dd=args.max_oos_dd,
            )
            print(f"  {'✓ ' + promote_msg if promote_msg else 'promotion blocked'}")
        ladder_status = ladder.status().to_dict()

    output_data: dict[str, Any] = {
        "wf_result": wf.to_dict(),
        "promote_message": promote_msg,
        "ladder_status": ladder_status,
        "timestamp": datetime.now().isoformat(),
    }
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(output_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if wf.passed else 2


if __name__ == "__main__":
    sys.exit(main())
