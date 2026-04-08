"""Walk-Forward Optimization (롤링 윈도우 OOS 검증)

N개 폴드로 데이터를 분할해 In-Sample 학습 → Out-of-Sample 검증을 반복.
과최적화를 탐지하고, 전략의 실전 적합성을 판별한다.

참고: Zipline walk-forward, QuantConnect rolling window 패턴

Usage:
    from kis_backtest.core.walk_forward import WalkForwardValidator, WFConfig

    validator = WalkForwardValidator(config=WFConfig(n_folds=5))
    result = validator.validate(
        returns=daily_returns,  # List[float]
        strategy_fn=my_strategy,  # Callable[[List[float]], List[float]]
    )

    if result.passed:
        print("OOS 검증 통과!")
    else:
        print(f"FAIL: OOS Sharpe {result.oos_mean_sharpe:.3f} < {result.min_sharpe}")
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WFConfig:
    """Walk-Forward 설정"""
    n_folds: int = 5                 # 폴드 수
    train_ratio: float = 0.7        # 각 폴드 내 학습 비율
    min_sharpe: float = 0.3         # OOS 최소 Sharpe
    max_oos_dd: float = -0.20       # OOS 최대 드로다운
    min_win_rate: float = 0.4       # 폴드 중 최소 통과 비율 (2/5 = 0.4)
    anchored: bool = False          # True면 학습 시작점 고정 (expanding window)
    annualization_factor: int = 252  # 연율화 계수


@dataclass(frozen=True)
class FoldResult:
    """단일 폴드 결과"""
    fold_idx: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int
    is_sharpe: float        # In-Sample Sharpe
    oos_sharpe: float       # Out-of-Sample Sharpe
    oos_return: float       # OOS 누적 수익률
    oos_max_dd: float       # OOS 최대 드로다운
    oos_n_days: int         # OOS 기간 (일)
    degradation: float      # IS → OOS Sharpe 감소율

    @property
    def passed(self) -> bool:
        """OOS Sharpe가 기준 이상이면 통과 (min_sharpe는 상위에서 체크)"""
        return self.oos_sharpe > 0


@dataclass
class WFResult:
    """Walk-Forward 전체 결과"""
    config: WFConfig
    folds: List[FoldResult]
    total_days: int
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    @property
    def oos_mean_sharpe(self) -> float:
        if not self.folds:
            return 0.0
        return sum(f.oos_sharpe for f in self.folds) / len(self.folds)

    @property
    def oos_median_sharpe(self) -> float:
        if not self.folds:
            return 0.0
        sorted_sharpes = sorted(f.oos_sharpe for f in self.folds)
        n = len(sorted_sharpes)
        if n % 2 == 0:
            return (sorted_sharpes[n // 2 - 1] + sorted_sharpes[n // 2]) / 2
        return sorted_sharpes[n // 2]

    @property
    def oos_worst_sharpe(self) -> float:
        if not self.folds:
            return 0.0
        return min(f.oos_sharpe for f in self.folds)

    @property
    def oos_worst_dd(self) -> float:
        if not self.folds:
            return 0.0
        return min(f.oos_max_dd for f in self.folds)

    @property
    def mean_degradation(self) -> float:
        """평균 IS→OOS Sharpe 감소율 (0.5 = 50% 감소)"""
        if not self.folds:
            return 0.0
        return sum(f.degradation for f in self.folds) / len(self.folds)

    @property
    def win_rate(self) -> float:
        """OOS Sharpe > 0인 폴드 비율"""
        if not self.folds:
            return 0.0
        wins = sum(1 for f in self.folds if f.passed)
        return wins / len(self.folds)

    @property
    def passed(self) -> bool:
        """종합 통과 판정"""
        return (
            self.oos_mean_sharpe >= self.config.min_sharpe
            and self.oos_worst_dd >= self.config.max_oos_dd
            and self.win_rate >= self.config.min_win_rate
        )

    @property
    def verdict(self) -> str:
        if self.passed:
            return "PASS"
        reasons = []
        if self.oos_mean_sharpe < self.config.min_sharpe:
            reasons.append(
                f"OOS Sharpe {self.oos_mean_sharpe:.3f} < {self.config.min_sharpe}"
            )
        if self.oos_worst_dd < self.config.max_oos_dd:
            reasons.append(
                f"OOS MaxDD {self.oos_worst_dd:.1%} < {self.config.max_oos_dd:.1%}"
            )
        if self.win_rate < self.config.min_win_rate:
            reasons.append(
                f"Win rate {self.win_rate:.0%} < {self.config.min_win_rate:.0%}"
            )
        return f"FAIL: {'; '.join(reasons)}"

    def summary_table(self) -> List[Dict[str, Any]]:
        """폴드별 요약 테이블"""
        return [
            {
                "fold": f.fold_idx + 1,
                "train": f"{f.train_start}-{f.train_end}",
                "test": f"{f.test_start}-{f.test_end}",
                "is_sharpe": round(f.is_sharpe, 3),
                "oos_sharpe": round(f.oos_sharpe, 3),
                "oos_return": f"{f.oos_return:.1%}",
                "oos_dd": f"{f.oos_max_dd:.1%}",
                "degrad": f"{f.degradation:.0%}",
                "pass": "O" if f.passed else "X",
            }
            for f in self.folds
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "verdict": self.verdict,
            "oos_mean_sharpe": round(self.oos_mean_sharpe, 4),
            "oos_median_sharpe": round(self.oos_median_sharpe, 4),
            "oos_worst_sharpe": round(self.oos_worst_sharpe, 4),
            "oos_worst_dd": round(self.oos_worst_dd, 4),
            "mean_degradation": round(self.mean_degradation, 4),
            "win_rate": round(self.win_rate, 4),
            "total_days": self.total_days,
            "n_folds": len(self.folds),
            "folds": self.summary_table(),
            "config": {
                "n_folds": self.config.n_folds,
                "train_ratio": self.config.train_ratio,
                "min_sharpe": self.config.min_sharpe,
                "max_oos_dd": self.config.max_oos_dd,
                "anchored": self.config.anchored,
            },
            "timestamp": self.timestamp,
        }


# ── 핵심 계산 함수 ────────────────────────────────────────────

def _sharpe(returns: Sequence[float], annual_factor: int = 252) -> float:
    """Sharpe ratio (rf=0 가정, 일간 수익률 기준)"""
    if len(returns) < 2:
        return 0.0
    n = len(returns)
    mean_r = sum(returns) / n
    var_r = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
    return (mean_r / std_r) * math.sqrt(annual_factor)


def _max_drawdown(returns: Sequence[float]) -> float:
    """최대 드로다운 (음수)"""
    if not returns:
        return 0.0
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1 + r)
        peak = max(peak, equity)
        dd = (equity - peak) / peak if peak > 0 else 0.0
        max_dd = min(max_dd, dd)
    return max_dd


def _cumulative_return(returns: Sequence[float]) -> float:
    """누적 수익률"""
    if not returns:
        return 0.0
    equity = 1.0
    for r in returns:
        equity *= (1 + r)
    return equity - 1.0


def _split_folds(
    n_total: int,
    n_folds: int,
    train_ratio: float,
    anchored: bool,
) -> List[Tuple[int, int, int, int]]:
    """폴드 인덱스 생성

    Returns:
        [(train_start, train_end, test_start, test_end), ...]
    """
    if anchored:
        # Expanding window: 학습 시작 고정, 점점 넓어짐
        test_size = n_total // (n_folds + 1)
        folds = []
        for i in range(n_folds):
            train_start = 0
            train_end = test_size * (i + 1)
            test_start = train_end
            test_end = min(train_end + test_size, n_total)
            if test_start < n_total:
                folds.append((train_start, train_end, test_start, test_end))
        return folds
    else:
        # Rolling window: 균등 분할
        fold_size = n_total // n_folds
        folds = []
        for i in range(n_folds):
            fold_start = i * fold_size
            fold_end = fold_start + fold_size if i < n_folds - 1 else n_total
            split = fold_start + int((fold_end - fold_start) * train_ratio)
            folds.append((fold_start, split, split, fold_end))
        return folds


# ── 메인 클래스 ────────────────────────────────────────────

StrategyFn = Callable[[List[float]], List[float]]
"""전략 함수 타입: 가격/수익률 입력 → 시그널 수익률 출력"""


class WalkForwardValidator:
    """Walk-Forward 검증기

    전략 함수를 받아 N-fold IS/OOS 분석을 수행한다.

    strategy_fn:
        학습 데이터(List[float])를 입력받아
        테스트 데이터에 적용할 시그널 수익률(List[float])을 반환.

        단순 예시 (buy & hold):
            def strategy(train_returns):
                return train_returns  # 그대로 반환

        SMA 전략 예시:
            def sma_strategy(train_returns):
                # train에서 파라미터 학습 → test에 적용
                return signal_returns
    """

    def __init__(self, config: Optional[WFConfig] = None):
        self.config = config or WFConfig()

    def validate(
        self,
        returns: Sequence[float],
        strategy_fn: Optional[StrategyFn] = None,
        oos_returns_per_fold: Optional[List[List[float]]] = None,
    ) -> WFResult:
        """Walk-Forward 검증 실행

        Args:
            returns: 전체 일간 수익률 시계열
            strategy_fn: 전략 함수 (train → test signal returns)
                         None이면 buy & hold로 OOS 수익률 그대로 사용
            oos_returns_per_fold: 직접 OOS 수익률 제공 (외부 백테스트 결과)
                                 이 경우 strategy_fn 무시

        Returns:
            WFResult: 폴드별 + 종합 결과
        """
        n_total = len(returns)
        if n_total < 60:
            logger.warning("데이터 부족: %d일 (최소 60일 필요)", n_total)
            return WFResult(
                config=self.config, folds=[], total_days=n_total,
            )

        folds_idx = _split_folds(
            n_total, self.config.n_folds,
            self.config.train_ratio, self.config.anchored,
        )

        fold_results: List[FoldResult] = []

        for i, (tr_s, tr_e, te_s, te_e) in enumerate(folds_idx):
            train_rets = list(returns[tr_s:tr_e])
            test_rets = list(returns[te_s:te_e])

            if len(test_rets) < 5:
                logger.warning("폴드 %d OOS 데이터 부족 (%d일), 스킵", i, len(test_rets))
                continue

            # IS metrics
            is_sharpe = _sharpe(train_rets, self.config.annualization_factor)

            # OOS metrics
            if oos_returns_per_fold and i < len(oos_returns_per_fold):
                oos_rets = oos_returns_per_fold[i]
            elif strategy_fn:
                oos_rets = strategy_fn(train_rets)
                # 길이 맞추기 — 전략이 다른 길이를 반환할 수 있음
                if len(oos_rets) != len(test_rets):
                    oos_rets = test_rets  # fallback
            else:
                oos_rets = test_rets

            oos_sharpe = _sharpe(oos_rets, self.config.annualization_factor)
            oos_ret = _cumulative_return(oos_rets)
            oos_dd = _max_drawdown(oos_rets)

            # IS→OOS degradation
            if abs(is_sharpe) > 0.01:
                degradation = 1.0 - (oos_sharpe / is_sharpe)
            else:
                degradation = 0.0

            fold_results.append(FoldResult(
                fold_idx=i,
                train_start=tr_s,
                train_end=tr_e,
                test_start=te_s,
                test_end=te_e,
                is_sharpe=is_sharpe,
                oos_sharpe=oos_sharpe,
                oos_return=oos_ret,
                oos_max_dd=oos_dd,
                oos_n_days=len(oos_rets),
                degradation=degradation,
            ))

            logger.info(
                "폴드 %d/%d: IS Sharpe=%.3f → OOS Sharpe=%.3f (degrad %.0f%%)",
                i + 1, self.config.n_folds, is_sharpe, oos_sharpe,
                degradation * 100,
            )

        result = WFResult(
            config=self.config,
            folds=fold_results,
            total_days=n_total,
        )

        logger.info(
            "Walk-Forward 결과: %s (평균 OOS Sharpe=%.3f, 승률=%.0f%%)",
            result.verdict, result.oos_mean_sharpe, result.win_rate * 100,
        )

        return result

    def validate_multi_asset(
        self,
        returns_dict: Dict[str, Sequence[float]],
        weights: Dict[str, float],
        strategy_fn: Optional[StrategyFn] = None,
    ) -> WFResult:
        """멀티 종목 포트폴리오 Walk-Forward

        종목별 수익률을 비중 가중합으로 합친 뒤 검증.
        """
        tickers = [t for t in weights if t in returns_dict]
        if not tickers:
            return WFResult(config=self.config, folds=[], total_days=0)

        min_len = min(len(returns_dict[t]) for t in tickers)
        port_returns: List[float] = []
        for i in range(min_len):
            day_ret = sum(
                weights.get(t, 0) * returns_dict[t][i]
                for t in tickers
            )
            port_returns.append(day_ret)

        return self.validate(port_returns, strategy_fn)
