"""실행 계층 데이터모델

PortfolioOrder(분석 결과) → PlannedTrade(주문 계획) → Order(KIS 체결)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

from kis_backtest.models import Order, OrderSide


class TradeReason(str, Enum):
    """거래 사유"""
    NEW_ENTRY = "new_entry"       # 신규 진입
    REBALANCE = "rebalance"       # 리밸런싱
    EXIT = "exit"                 # 전량 매도
    REDUCE = "reduce"             # 비중 축소
    DD_REDUCE = "dd_reduce"       # 드로다운 강제 축소


@dataclass(frozen=True)
class TransactionCostEstimate:
    """거래비용 사전 추정"""
    commission: float       # 수수료 (원)
    tax: float              # 세금 (원, 매도 시)
    slippage: float         # 슬리피지 추정 (원)

    @property
    def total(self) -> float:
        return self.commission + self.tax + self.slippage


@dataclass(frozen=True)
class PlannedTrade:
    """계획된 단일 거래

    PortfolioOrder의 StockAllocation에서 변환.
    실제 주문 전 검증/승인 단계에서 사용.
    """
    symbol: str
    name: str
    side: OrderSide
    quantity: int
    estimated_price: float
    estimated_cost: TransactionCostEstimate
    reason: TradeReason
    target_weight: float = 0.0          # 목표 비중
    current_weight: float = 0.0         # 현재 비중

    @property
    def estimated_amount(self) -> float:
        """추정 거래금액 (원)"""
        return self.quantity * self.estimated_price

    @property
    def total_cost_with_fees(self) -> float:
        """수수료 포함 총 비용"""
        return self.estimated_amount + self.estimated_cost.total

    def summary_line(self) -> str:
        side_kr = "매수" if self.side == OrderSide.BUY else "매도"
        return (
            f"{side_kr} {self.name}({self.symbol}) "
            f"{self.quantity}주 × {self.estimated_price:,.0f}원 "
            f"= {self.estimated_amount:,.0f}원 "
            f"(비용 {self.estimated_cost.total:,.0f}원)"
        )


@dataclass
class ExecutionReport:
    """주문 실행 리포트

    LiveOrderExecutor.execute()의 반환값.
    계획 대비 실행 결과 추적.
    """
    planned: List[PlannedTrade]
    executed: List[Order] = field(default_factory=list)
    rejected: List[Tuple[PlannedTrade, str]] = field(default_factory=list)
    skipped: List[Tuple[PlannedTrade, str]] = field(default_factory=list)
    total_commission: float = 0.0
    total_slippage_estimate: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def n_planned(self) -> int:
        return len(self.planned)

    @property
    def n_executed(self) -> int:
        return len(self.executed)

    @property
    def n_rejected(self) -> int:
        return len(self.rejected)

    @property
    def n_skipped(self) -> int:
        return len(self.skipped)

    @property
    def execution_rate(self) -> float:
        """실행률"""
        if self.n_planned == 0:
            return 0.0
        return self.n_executed / self.n_planned

    @property
    def total_buy_amount(self) -> float:
        return sum(
            t.estimated_amount for t in self.planned
            if t.side == OrderSide.BUY
        )

    @property
    def total_sell_amount(self) -> float:
        return sum(
            t.estimated_amount for t in self.planned
            if t.side == OrderSide.SELL
        )

    def summary(self) -> str:
        lines = [
            "=== 실행 리포트 ===",
            f"시간: {self.timestamp:%Y-%m-%d %H:%M:%S}",
            f"계획: {self.n_planned}건 | 실행: {self.n_executed}건 | "
            f"거절: {self.n_rejected}건 | 스킵: {self.n_skipped}건",
            f"실행률: {self.execution_rate*100:.0f}%",
            f"매수 총액: {self.total_buy_amount:,.0f}원",
            f"매도 총액: {self.total_sell_amount:,.0f}원",
            f"추정 수수료: {self.total_commission:,.0f}원",
        ]
        if self.planned:
            lines.append("")
            lines.append("--- 거래 내역 ---")
            for trade in self.planned:
                lines.append(f"  {trade.summary_line()}")
        if self.rejected:
            lines.append("")
            lines.append("--- 거절 ---")
            for trade, reason in self.rejected:
                lines.append(f"  ✗ {trade.name}: {reason}")
        if self.skipped:
            lines.append("")
            lines.append("--- 스킵 ---")
            for trade, reason in self.skipped:
                lines.append(f"  - {trade.name}: {reason}")
        return "\n".join(lines)
