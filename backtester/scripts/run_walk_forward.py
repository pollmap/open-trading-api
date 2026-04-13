#!/usr/bin/env python
"""Walk-Forward OOS 4주 검증 + CapitalLadder 자동 승급 (v0.8 STEP 4).

equity curve(JSON) → WFValidator 롤링 N-fold 검증 → CapitalLadder.promote_if_wf_passed

Usage:
    python scripts/run_walk_forward.py \\
        --equity-file data/equity_curve.json \\
        --n-folds 5 \\
        --train-ratio 0.7 \\
        --ladder-state data/ladder_state.json \\
        --auto-promote

equity JSON 포맷:
    [
        {"date": "2026-01-01", "equity": 10000000, "daily_return": 0.002},
        ...
    ]
또는 단순:
    {"returns": [0.002, -0.001, ...]}
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from kis_backtest.core.walk_forward import (
    WalkForwardValidator,
    WFConfig,
    WFResult,
)
from kis_backtest.execution.capital_ladder import (
    CapitalLadder,
    LadderConfig,
)

logger = logging.getLogger("run_walk_forward")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-Forward OOS 검증")
    p.add_argument("--equity-file", required=True,
                   help="equity curve JSON 파일 경로")
    p.add_argument("--n-folds", type=int, default=5,
                   help="WF 폴드 수 (기본 5)")
    p.add_argument("--train-ratio", type=float, default=0.7,
                   help="폴드 내 학습 비율 (기본 0.7)")
    p.add_argument("--min-oos-sharpe", type=float, default=0.5,
                   help="승급 최소 OOS Sharpe (기본 0.5)")
    p.add_argument("--max-oos-dd", type=float, default=-0.10,
                   help="승급 최대 OOS MaxDD (기본 -10%)")
    p.add_argument("--ladder-state",
                   help="CapitalLadder state_file 경로 (선택)")
    p.add_argument("--total-capital", type=float, default=10_000_000,
                   help="래더 총 자본 (기본 10,000,000원)")
    p.add_argument("--auto-promote", action="store_true",
                   help="WF 통과 시 자동 승급 수행")
    p.add_argument("--output",
                   help="결과 JSON 저장 경로 (기본: stdout)")
    return p.parse_args()


def load_returns_from_file(path: str | Path) -> list[float]:
    """equity JSON → daily_return 리스트.

    두 포맷 지원:
      (a) [{"date":..., "equity":..., "daily_return":...}, ...]
      (b) {"returns": [0.002, -0.001, ...]}

    daily_return이 없으면 equity 연속값에서 계산.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    if isinstance(data, dict) and "returns" in data:
        return [float(r) for r in data["returns"]]

    if not isinstance(data, list):
        raise ValueError(f"지원되지 않는 포맷: {type(data).__name__}")

    if data and "daily_return" in data[0]:
        return [float(row["daily_return"]) for row in data[1:]]  # 첫날은 ret=0 제외

    # equity만 있으면 계산
    equities = [float(row["equity"]) for row in data]
    returns: list[float] = []
    for i in range(1, len(equities)):
        prev = equities[i - 1]
        if prev > 0:
            returns.append((equities[i] - prev) / prev)
    return returns


def _identity_strategy(returns):
    """기본 전략: 입력 returns 그대로 반환 (passive benchmark)."""
    return list(returns)


def run_walk_forward(
    returns: list[float],
    n_folds: int,
    train_ratio: float,
    min_sharpe: float = 0.5,
    max_dd: float = -0.10,
) -> WFResult:
    """WFValidator 실행 헬퍼."""
    config = WFConfig(
        n_folds=n_folds,
        train_ratio=train_ratio,
        min_sharpe=min_sharpe,
        max_oos_dd=max_dd,
    )
    validator = WalkForwardValidator(config=config)
    return validator.validate(returns=returns, strategy_fn=_identity_strategy)


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print("  Luxon Quant — Walk-Forward OOS 검증")
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)

    # 1. equity → returns
    try:
        returns = load_returns_from_file(args.equity_file)
    except Exception as exc:
        print(f"\n[오류] equity 로드 실패: {exc}")
        return 1

    print(f"\n[1/3] equity 로드: {len(returns)}일 return 시계열")
    if len(returns) < args.n_folds * 5:
        print(
            f"  경고: {len(returns)}일은 {args.n_folds}-fold에 짧음 "
            f"(권장 ≥ {args.n_folds * 20}일)"
        )

    # 2. WF 검증
    print(f"\n[2/3] WF 실행: n_folds={args.n_folds}, "
          f"train_ratio={args.train_ratio}")
    wf_result = run_walk_forward(
        returns=returns,
        n_folds=args.n_folds,
        train_ratio=args.train_ratio,
        min_sharpe=args.min_oos_sharpe,
        max_dd=args.max_oos_dd,
    )

    print(f"\n  판정: {wf_result.verdict}")
    print(f"  OOS 평균 Sharpe: {wf_result.oos_mean_sharpe:.3f}")
    print(f"  OOS 최악 Sharpe: {wf_result.oos_worst_sharpe:.3f}")
    print(f"  OOS 최악 DD:     {wf_result.oos_worst_dd:.1%}")
    print(f"  승률 (폴드):     {wf_result.win_rate:.0%}")
    print(f"  IS→OOS 감소율:   {wf_result.mean_degradation:.0%}")

    # 3. CapitalLadder 자동 승급
    promote_msg: Optional[str] = None
    ladder_status: Optional[dict] = None
    if args.ladder_state:
        print(f"\n[3/3] CapitalLadder 연동 (state={args.ladder_state})")
        ladder = CapitalLadder(LadderConfig(
            total_capital=args.total_capital,
            state_file=args.ladder_state,
        ))
        print(f"  현재 단계: {ladder.current_stage.name} "
              f"({ladder.current_stage_config.label})")
        print(f"  배포 자본: {ladder.deployed_capital:,.0f}원")

        if args.auto_promote:
            promote_msg = ladder.promote_if_wf_passed(
                wf_result,
                min_oos_sharpe=args.min_oos_sharpe,
                max_oos_dd=args.max_oos_dd,
            )
            if promote_msg:
                print(f"\n  ✓ {promote_msg}")
            else:
                print("\n  승급 조건 미달 — 현 단계 유지")
        ladder_status = ladder.status().to_dict()
    else:
        print("\n[3/3] CapitalLadder 스킵 (--ladder-state 미지정)")

    # 4. 결과 출력
    output_data: dict[str, Any] = {
        "wf_result": wf_result.to_dict(),
        "promote_message": promote_msg,
        "ladder_status": ladder_status,
    }
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(output_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n결과 저장: {out_path}")
    print("\n" + "=" * 60)
    print(f"  {'PASS' if wf_result.passed else 'FAIL'} — "
          f"{'승급 추천' if wf_result.passed else '더 많은 페이퍼 데이터 필요'}")
    print("=" * 60)
    return 0 if wf_result.passed else 2


if __name__ == "__main__":
    sys.exit(main())
