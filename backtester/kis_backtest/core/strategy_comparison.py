"""멀티 전략 백테스트 비교 러너

N개 전략을 동일 유니버스/기간에서 실행하고,
Sharpe/MDD/비용 기준으로 비교 테이블을 생성한다.

BL vs HRP 포트폴리오 최적화 비교도 포함.

Usage:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
    from kis_backtest.core.strategy_comparison import StrategyComparison

    provider = MCPDataProvider()
    comp = StrategyComparison(provider, symbols=["005930", "000660"])
    result = comp.run_comparison_sync()

    for s in result.strategies:
        print(f"{s.strategy_name}: Sharpe={s.sharpe}, MDD={s.max_drawdown}")
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

from kis_backtest.portfolio.factor_to_views import (
    factor_scores_to_bl_views,
    bl_views_to_mcp_format,
)

logger = logging.getLogger(__name__)


@dataclass
class StrategyResult:
    """단일 전략 백테스트 결과"""
    strategy_id: str
    strategy_name: str
    sharpe: Optional[float] = None
    annual_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    total_trades: int = 0
    annual_cost: float = 0.0
    status: str = "pending"  # pending, completed, failed, timeout
    raw_result: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ComparisonResult:
    """멀티 전략 비교 결과"""
    strategies: List[StrategyResult]
    symbols: List[str]
    start_date: str
    end_date: str
    bl_weights: Dict[str, float] = field(default_factory=dict)
    hrp_weights: Dict[str, float] = field(default_factory=dict)
    recommended_weights: Dict[str, float] = field(default_factory=dict)
    recommendation: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    @property
    def best_by_sharpe(self) -> Optional[StrategyResult]:
        completed = [s for s in self.strategies if s.sharpe is not None]
        return max(completed, key=lambda s: s.sharpe) if completed else None

    @property
    def completed_count(self) -> int:
        return sum(1 for s in self.strategies if s.status == "completed")

    def ranking_table(self) -> List[Dict[str, Any]]:
        """Sharpe 기준 내림차순 정렬 테이블"""
        completed = [s for s in self.strategies if s.status == "completed"]
        ranked = sorted(completed, key=lambda s: s.sharpe or 0, reverse=True)
        return [
            {
                "rank": i + 1,
                "strategy": s.strategy_name,
                "sharpe": round(s.sharpe, 3) if s.sharpe else None,
                "annual_return": f"{s.annual_return*100:.1f}%" if s.annual_return else None,
                "max_drawdown": f"{s.max_drawdown*100:.1f}%" if s.max_drawdown else None,
                "trades": s.total_trades,
                "cost": f"{s.annual_cost*100:.2f}%",
            }
            for i, s in enumerate(ranked)
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategies": [
                {
                    "strategy_id": s.strategy_id,
                    "strategy_name": s.strategy_name,
                    "sharpe": s.sharpe,
                    "annual_return": s.annual_return,
                    "max_drawdown": s.max_drawdown,
                    "win_rate": s.win_rate,
                    "total_trades": s.total_trades,
                    "annual_cost": s.annual_cost,
                    "status": s.status,
                }
                for s in self.strategies
            ],
            "symbols": self.symbols,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "bl_weights": self.bl_weights,
            "hrp_weights": self.hrp_weights,
            "recommended_weights": self.recommended_weights,
            "recommendation": self.recommendation,
            "ranking": self.ranking_table(),
            "timestamp": self.timestamp,
        }


# 기본 비교 전략 3개
DEFAULT_STRATEGIES = ["sma_crossover", "momentum", "volatility_breakout"]

# 전략 이름 매핑
STRATEGY_NAMES = {
    "sma_crossover": "SMA 골든/데드 크로스",
    "momentum": "모멘텀 (ROC)",
    "volatility_breakout": "변동성 돌파",
    "week52_high": "52주 신고가",
    "consecutive_moves": "연속 움직임",
    "ma_divergence": "이평선 괴리",
    "false_breakout": "거짓 돌파",
    "strong_close": "강한 종가",
    "short_term_reversal": "단기 반전",
    "trend_filter_signal": "추세 필터 시그널",
}


class StrategyComparison:
    """멀티 전략 백테스트 비교 러너

    KIS Backtest MCP(127.0.0.1:3846)를 통해 N개 전략을 동일 조건에서 실행하고,
    Sharpe/MDD/비용 기준으로 비교한다.
    """

    def __init__(
        self,
        mcp: "MCPDataProvider",
        symbols: List[str],
        start_date: str = "2021-01-01",
        end_date: str = "2026-04-05",
        initial_capital: float = 10_000_000,
    ):
        self._mcp = mcp
        self._symbols = symbols
        self._start_date = start_date
        self._end_date = end_date
        self._initial_capital = initial_capital

    async def run_comparison(
        self,
        strategy_ids: Optional[List[str]] = None,
    ) -> ComparisonResult:
        """N개 전략을 동일 유니버스에서 실행 → 비교"""
        ids = strategy_ids or DEFAULT_STRATEGIES
        results: List[StrategyResult] = []

        for sid in ids:
            sr = await self._run_single_strategy(sid)
            results.append(sr)
            logger.info(
                "전략 %s: %s (Sharpe=%s)",
                sid, sr.status,
                f"{sr.sharpe:.3f}" if sr.sharpe else "N/A",
            )

        return ComparisonResult(
            strategies=results,
            symbols=self._symbols,
            start_date=self._start_date,
            end_date=self._end_date,
        )

    async def _run_single_strategy(self, strategy_id: str) -> StrategyResult:
        """단일 전략 백테스트 실행 + 메트릭 추출"""
        name = STRATEGY_NAMES.get(strategy_id, strategy_id)
        sr = StrategyResult(
            strategy_id=strategy_id,
            strategy_name=name,
        )

        try:
            bt = await self._mcp.run_and_wait_backtest(
                strategy_id=strategy_id,
                symbols=self._symbols,
                start_date=self._start_date,
                end_date=self._end_date,
                initial_capital=self._initial_capital,
            )
            sr.raw_result = bt
            sr.status = bt.get("status", "unknown")

            if sr.status == "completed":
                self._extract_metrics(sr, bt)
        except Exception as e:
            sr.status = "failed"
            sr.raw_result = {"error": str(e)}
            logger.warning("전략 %s 실행 실패: %s", strategy_id, e)

        return sr

    @staticmethod
    def _extract_metrics(sr: StrategyResult, bt: Dict[str, Any]) -> None:
        """백테스트 결과에서 메트릭 추출"""
        result = bt.get("result", bt)
        metrics = result.get("metrics", {})

        # risk 메트릭
        risk = metrics.get("risk", {})
        sr.sharpe = _safe_float(risk.get("sharpe_ratio"))

        # basic 메트릭
        basic = metrics.get("basic", {})
        sr.annual_return = _safe_float(basic.get("annual_return", basic.get("annualized_return")))
        sr.max_drawdown = _safe_float(basic.get("max_drawdown"))
        sr.win_rate = _safe_float(basic.get("win_rate"))
        sr.total_trades = int(basic.get("total_trades", 0))

        # 비용 (있으면)
        cost = metrics.get("cost", {})
        sr.annual_cost = _safe_float(cost.get("annual_cost")) or 0.0

    async def optimize_portfolio(
        self,
        returns_dict: Dict[str, List[float]],
        factor_scores: Dict[str, Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """BL + HRP 비중 동시 계산 → 비교

        Returns:
            (bl_weights, hrp_weights)
        """
        # 팩터 → BL 뷰 변환
        views = factor_scores_to_bl_views(factor_scores)
        mcp_views = bl_views_to_mcp_format(views)

        # 병렬 실행
        bl_task = self._mcp.get_bl_weights(returns_dict, views=mcp_views)
        hrp_task = self._mcp.get_hrp_weights(returns_dict)

        bl_weights, hrp_weights = await asyncio.gather(
            bl_task, hrp_task, return_exceptions=True
        )

        # 에러 핸들링
        if isinstance(bl_weights, Exception):
            logger.warning("BL 최적화 실패: %s", bl_weights)
            bl_weights = {}
        if isinstance(hrp_weights, Exception):
            logger.warning("HRP 최적화 실패: %s", hrp_weights)
            hrp_weights = {}

        return bl_weights, hrp_weights

    def select_weights(
        self,
        bl_weights: Dict[str, float],
        hrp_weights: Dict[str, float],
    ) -> Tuple[Dict[str, float], str]:
        """BL vs HRP 중 추천 비중 선택

        HRP 우선 (더 robust) — BL이 모든 비중 > 0이고 분산이 적으면 BL 선택.
        """
        if not bl_weights and not hrp_weights:
            return {}, "EQUAL: 최적화 결과 없음 — 동일비중 권장"

        if not bl_weights:
            return hrp_weights, "HRP: BL 실패, HRP 사용"

        if not hrp_weights:
            return bl_weights, "BL: HRP 실패, BL 사용"

        # 둘 다 있으면: 분산도 비교 (비중 표준편차)
        import math
        bl_vals = list(bl_weights.values())
        hrp_vals = list(hrp_weights.values())

        bl_std = math.sqrt(sum((v - 1/len(bl_vals))**2 for v in bl_vals) / len(bl_vals)) if bl_vals else 1.0
        hrp_std = math.sqrt(sum((v - 1/len(hrp_vals))**2 for v in hrp_vals) / len(hrp_vals)) if hrp_vals else 1.0

        # HRP는 보통 더 분산된 비중 → 더 robust
        if hrp_std < bl_std * 0.8:
            return hrp_weights, "HRP: 더 균등한 분산 (robust)"
        return bl_weights, "BL: 뷰 반영된 집중 투자"

    def save(self, result: ComparisonResult) -> Path:
        """결과를 JSON으로 저장"""
        return self._mcp.save_result(
            result.to_dict(),
            category="comparison",
            tag=f"{len(result.strategies)}strategies",
        )

    # ── sync 래퍼 ─────────────────────────────────────────────

    def run_comparison_sync(
        self,
        strategy_ids: Optional[List[str]] = None,
    ) -> ComparisonResult:
        from kis_backtest.portfolio.mcp_data_provider import _run_sync
        return _run_sync(self.run_comparison(strategy_ids))

    def optimize_portfolio_sync(
        self,
        returns_dict: Dict[str, List[float]],
        factor_scores: Dict[str, Dict[str, Any]],
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        from kis_backtest.portfolio.mcp_data_provider import _run_sync
        return _run_sync(self.optimize_portfolio(returns_dict, factor_scores))


def _safe_float(val: Any) -> Optional[float]:
    """안전한 float 변환 — None, str, int 모두 처리"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
