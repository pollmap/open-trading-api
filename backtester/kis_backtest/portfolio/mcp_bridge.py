"""MCP → KIS 브릿지

Nexus Finance MCP 364도구의 분석 결과를 KIS 백테스트/실행 시스템으로 변환.
이 모듈이 "분석"과 "실행" 사이의 핵심 연결고리.

Flow:
    MCP factor_score() → 종목별 팩터 점수
    MCP portadv_black_litterman() → 최적 비중
    → PortfolioOrder (이 모듈)
    → KIS backtester 또는 KIS order-executor
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from kis_backtest.strategies.risk.cost_model import (
    KoreaTransactionCostModel,
    Market,
)
from kis_backtest.strategies.risk.drawdown_guard import (
    ConcentrationLimits,
    check_concentration,
)
from kis_backtest.strategies.risk.vol_target import VolatilityTargeter


class OrderAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class StockAllocation:
    """단일 종목 배분"""
    ticker: str
    name: str
    market: Market
    target_weight: float        # 목표 비중 (0~1)
    factor_score: float         # 팩터 종합 점수
    sector: str = ""
    current_weight: float = 0.0 # 현재 비중
    action: OrderAction = OrderAction.HOLD
    trade_amount: float = 0.0   # 거래 금액 (원)


@dataclass
class PortfolioOrder:
    """포트폴리오 주문 지시서

    MCP 분석 결과를 실행 가능한 형태로 변환한 최종 산출물.
    KIS order-executor 또는 backtester에 전달.
    """
    strategy_name: str
    created_at: datetime
    total_capital: float                        # 총 투자 가능 자금 (원)
    allocations: List[StockAllocation]          # 종목별 배분
    cash_weight: float                          # 현금 비중
    cost_model: KoreaTransactionCostModel       # 거래비용 모델
    estimated_annual_cost: float                # 추정 연간 거래비용률
    kelly_fraction: float                       # Kelly 적용 비율
    risk_gate_passed: bool                      # 리스크 게이트 통과 여부
    risk_gate_details: List[str]                # 리스크 체크 상세
    rebalance_frequency: str                    # 리밸런싱 주기

    @property
    def n_stocks(self) -> int:
        return len([a for a in self.allocations if a.target_weight > 0])

    @property
    def total_weight(self) -> float:
        return sum(a.target_weight for a in self.allocations) + self.cash_weight

    def summary(self) -> str:
        lines = [
            f"=== {self.strategy_name} ===",
            f"생성: {self.created_at:%Y-%m-%d %H:%M}",
            f"총 자금: {self.total_capital:,.0f}원",
            f"종목수: {self.n_stocks}개 | 현금: {self.cash_weight*100:.1f}%",
            f"리밸런싱: {self.rebalance_frequency}",
            f"추정 연간비용: {self.estimated_annual_cost*100:.2f}%",
            f"Kelly 배수: {self.kelly_fraction:.1f}x",
            f"리스크 게이트: {'PASS' if self.risk_gate_passed else 'FAIL'}",
        ]
        if not self.risk_gate_passed:
            for detail in self.risk_gate_details:
                lines.append(f"  !! {detail}")
        lines.append("")
        lines.append(f"{'종목':>10} {'비중':>8} {'팩터':>8} {'섹터':>10} {'행동':>6}")
        lines.append("-" * 50)
        for a in sorted(self.allocations, key=lambda x: -x.target_weight):
            if a.target_weight > 0.001:
                lines.append(
                    f"{a.name:>10} {a.target_weight*100:>7.1f}% "
                    f"{a.factor_score:>7.2f} {a.sector:>10} {a.action.value:>6}"
                )
        return "\n".join(lines)


class MCPBridge:
    """MCP 분석 결과 → 실행 가능한 포트폴리오 변환

    Usage:
        bridge = MCPBridge(total_capital=5_000_000)

        # MCP 결과를 받아서 변환
        order = bridge.build_order(
            strategy_name="한국 멀티팩터 월간",
            factor_scores={
                "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
                "000660": {"name": "SK하이닉스", "score": 0.75, "sector": "IT"},
                "051910": {"name": "LG화학", "score": 0.68, "sector": "화학"},
            },
            optimal_weights={
                "005930": 0.15,
                "000660": 0.12,
                "051910": 0.10,
            },
            rebalance_frequency="monthly",
        )

        print(order.summary())
        if order.risk_gate_passed:
            # KIS 실행으로 전달
            pass
    """

    def __init__(
        self,
        total_capital: float = 5_000_000,
        cost_model: Optional[KoreaTransactionCostModel] = None,
        vol_targeter: Optional[VolatilityTargeter] = None,
        concentration_limits: Optional[ConcentrationLimits] = None,
        kelly_fraction: float = 0.5,
        min_sharpe: float = 0.5,
        max_drawdown: float = -0.20,
    ):
        self.total_capital = total_capital
        self.cost_model = cost_model or KoreaTransactionCostModel()
        self.vol_targeter = vol_targeter or VolatilityTargeter(target_vol=0.10, max_leverage=1.5)
        self.concentration = concentration_limits or ConcentrationLimits()
        self.kelly_fraction = kelly_fraction
        self.min_sharpe = min_sharpe
        self.max_drawdown = max_drawdown

    def build_order(
        self,
        strategy_name: str,
        factor_scores: Dict[str, Dict],
        optimal_weights: Dict[str, float],
        rebalance_frequency: str = "monthly",
        current_weights: Optional[Dict[str, float]] = None,
        backtest_sharpe: Optional[float] = None,
        backtest_max_dd: Optional[float] = None,
    ) -> PortfolioOrder:
        """MCP 분석 결과 → PortfolioOrder 변환

        Args:
            factor_scores: {ticker: {"name": str, "score": float, "sector": str}}
            optimal_weights: {ticker: weight} (MCP BL/HRP 결과)
            rebalance_frequency: "monthly", "quarterly", "weekly"
            current_weights: 현재 보유 비중 (리밸런싱용)
            backtest_sharpe: 백테스트 Sharpe (리스크 게이트)
            backtest_max_dd: 백테스트 MaxDD (리스크 게이트)
        """
        current_weights = current_weights or {}

        # 리밸런싱 빈도 → 연간 RT
        freq_to_rt = {
            "weekly": 50,
            "biweekly": 24,
            "monthly": 12,
            "quarterly": 4,
        }
        n_rt = freq_to_rt.get(rebalance_frequency, 12)

        # 연간 비용 추정
        annual_cost = self.cost_model.annual_cost(n_rt)

        # 종목별 배분 생성
        allocations = []
        for ticker, weight in optimal_weights.items():
            info = factor_scores.get(ticker, {})
            cur_w = current_weights.get(ticker, 0.0)

            if weight > cur_w + 0.01:
                action = OrderAction.BUY
            elif weight < cur_w - 0.01:
                action = OrderAction.SELL
            else:
                action = OrderAction.HOLD

            trade_amount = abs(weight - cur_w) * self.total_capital

            allocations.append(StockAllocation(
                ticker=ticker,
                name=info.get("name", ticker),
                market=Market(info.get("market", "KOSPI")),
                target_weight=weight,
                factor_score=info.get("score", 0.0),
                sector=info.get("sector", ""),
                current_weight=cur_w,
                action=action,
                trade_amount=trade_amount,
            ))

        # 현금 비중
        total_stock_weight = sum(a.target_weight for a in allocations)
        cash_weight = max(0.0, 1.0 - total_stock_weight)

        # 리스크 게이트 체크
        gate_details = []
        gate_passed = True

        # 1. 집중도 체크
        weights_dict = {a.ticker: a.target_weight for a in allocations}
        sectors_dict = {a.ticker: a.sector for a in allocations if a.sector}
        conc = check_concentration(weights_dict, sectors_dict, self.concentration)
        if conc["violations"]:
            gate_passed = False
            gate_details.extend(conc["violations"])

        # 2. Sharpe 체크
        if backtest_sharpe is not None and backtest_sharpe < self.min_sharpe:
            gate_passed = False
            gate_details.append(
                f"After-cost Sharpe {backtest_sharpe:.2f} < {self.min_sharpe:.2f}"
            )

        # 3. MaxDD 체크
        if backtest_max_dd is not None and backtest_max_dd < self.max_drawdown:
            gate_passed = False
            gate_details.append(
                f"MaxDD {backtest_max_dd*100:.1f}% < {self.max_drawdown*100:.0f}% 한도"
            )

        # 4. 총 비중 체크
        if total_stock_weight > 1.05:
            gate_passed = False
            gate_details.append(
                f"총 비중 {total_stock_weight*100:.1f}% > 105%"
            )

        if not gate_details:
            gate_details.append("ALL PASS")

        return PortfolioOrder(
            strategy_name=strategy_name,
            created_at=datetime.now(),
            total_capital=self.total_capital,
            allocations=allocations,
            cash_weight=cash_weight,
            cost_model=self.cost_model,
            estimated_annual_cost=annual_cost,
            kelly_fraction=self.kelly_fraction,
            risk_gate_passed=gate_passed,
            risk_gate_details=gate_details,
            rebalance_frequency=rebalance_frequency,
        )
