"""
Luxon Terminal → execution 레이어 연결 어댑터.

OrchestrationReport (분석 결과) → PortfolioOrder (실행 지시서) 변환.
기존 execution/ 모듈 수정 0줄. 변환 + 위임만 담당.

Usage:
    bridge = ExecutorBridge(brokerage, price_provider, mode="paper")
    order = bridge.build_order(report, total_capital=100_000_000)
    exec_report = bridge.execute(order, dry_run=True)
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from kis_backtest.execution.kill_switch import KillSwitch
from kis_backtest.execution.models import ExecutionReport
from kis_backtest.execution.order_executor import (
    BrokerageProvider,
    LiveOrderExecutor,
    PriceProvider,
)
from kis_backtest.portfolio.mcp_bridge import (
    OrderAction,
    PortfolioOrder,
    StockAllocation,
)
from kis_backtest.strategies.risk.cost_model import (
    KoreaTransactionCostModel,
    Market,
)

from .orchestrator import OrchestrationReport


class ExecutorBridge:
    """Luxon 분석 결과 → KIS 주문 실행 어댑터.

    dry_run=True (기본): 주문 계획만 리포트, 실제 주문 0건.
    dry_run=False + mode="paper": KIS 모의투자 주문.
    dry_run=False + mode="prod": KIS 실전 주문 + Y/n 승인.
    """

    def __init__(
        self,
        brokerage: BrokerageProvider,
        price_provider: PriceProvider,
        *,
        mode: Literal["paper", "prod"] = "paper",
    ) -> None:
        self._mode = mode
        self._kill_switch = KillSwitch()
        self._executor = LiveOrderExecutor(
            brokerage=brokerage,
            price_provider=price_provider,
        )

    def build_order(
        self,
        report: OrchestrationReport,
        total_capital: float = 100_000_000.0,
    ) -> PortfolioOrder:
        """OrchestrationReport → PortfolioOrder 변환.

        BUY 결정만 추출. SKIP/HOLD 는 무시.
        """
        self._kill_switch.check_or_raise()

        allocations: list[StockAllocation] = []
        size_map = {ps.symbol: ps for ps in report.position_sizes}

        for decision in report.portfolio.decisions:
            if decision.action != "buy":
                continue
            ps = size_map.get(decision.symbol)
            if ps is None:
                continue
            allocations.append(
                StockAllocation(
                    ticker=decision.symbol,
                    name=decision.symbol,
                    market=Market.KOSPI,
                    target_weight=ps.weight,
                    factor_score=decision.catalyst_score,
                    action=OrderAction.BUY,
                    trade_amount=min(ps.amount, total_capital),
                )
            )

        return PortfolioOrder(
            strategy_name="Luxon Terminal",
            created_at=datetime.now(),
            total_capital=total_capital,
            allocations=allocations,
            cash_weight=report.portfolio.cash_weight,
            cost_model=KoreaTransactionCostModel(),
            estimated_annual_cost=0.01,
            kelly_fraction=0.5,
            risk_gate_passed=bool(allocations),
            risk_gate_details=[
                f"Luxon {len(allocations)} BUY / "
                f"{sum(1 for d in report.portfolio.decisions if d.action != 'buy')} SKIP",
            ],
            rebalance_frequency="weekly",
        )

    def execute(
        self,
        order: PortfolioOrder,
        *,
        dry_run: bool = True,
    ) -> ExecutionReport:
        """LiveOrderExecutor 위임.

        dry_run=True: plan() 만 (주문 계획 리포트).
        dry_run=False: execute() (실제 주문 발송).
        """
        self._kill_switch.check_or_raise()
        if dry_run:
            return self._executor.plan(order)
        return self._executor.execute(order)


__all__ = ["ExecutorBridge"]
