"""시그널 → 주문 실행 브릿지

PortfolioOrder(분석 결과)를 KISBrokerageProvider.submit_order()로 연결.

Flow:
    PortfolioOrder (from QuantPipeline)
      → weight → quantity 변환
      → 매도 먼저 (현금 확보)
      → 매수 실행
      → ExecutionReport 반환

한국 개인투자자 제약:
    - 공매도 불가 (short clipping은 pipeline에서 이미 처리)
    - 마진 불가 → 매도 먼저 실행해서 현금 확보
    - 최소 거래 단위: 1주
    - 호가 단위 준수 (지정가 시)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Protocol

from kis_backtest.models import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
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
from kis_backtest.execution.models import (
    ExecutionReport,
    PlannedTrade,
    TradeReason,
    TransactionCostEstimate,
)

logger = logging.getLogger(__name__)

# 최소 거래금액 — 이 이하는 슬리피지+수수료 대비 비효율
MIN_TRADE_AMOUNT_KRW = 50_000


class PriceProvider(Protocol):
    """현재가 조회 인터페이스"""
    def get_current_price(self, symbol: str) -> float: ...


class BrokerageProvider(Protocol):
    """브로커리지 인터페이스 (테스트 용이성)"""
    def get_balance(self) -> AccountBalance: ...
    def get_positions(self) -> List[Position]: ...
    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType,
        price: Optional[float],
    ) -> Order: ...


class LiveOrderExecutor:
    """PortfolioOrder → KIS 주문 실행

    Usage:
        executor = LiveOrderExecutor(brokerage, price_provider)
        report = executor.execute(portfolio_order)
        print(report.summary())
    """

    def __init__(
        self,
        brokerage: BrokerageProvider,
        price_provider: PriceProvider,
        cost_model: Optional[KoreaTransactionCostModel] = None,
        min_trade_amount: float = MIN_TRADE_AMOUNT_KRW,
    ):
        self._brokerage = brokerage
        self._price_provider = price_provider
        self._cost_model = cost_model or KoreaTransactionCostModel()
        self._min_trade_amount = min_trade_amount

    def execute(
        self,
        order: PortfolioOrder,
        dry_run: bool = False,
    ) -> ExecutionReport:
        """PortfolioOrder를 KIS 주문으로 변환 + 실행

        Args:
            order: QuantPipeline에서 생성된 포트폴리오 주문 지시서
            dry_run: True면 주문 제출 없이 PlannedTrade 목록만 반환

        Returns:
            ExecutionReport: 계획/실행/거절/스킵 내역
        """
        if not order.risk_gate_passed:
            logger.warning("리스크 게이트 미통과 — 실행 중단")
            return ExecutionReport(
                planned=[],
                rejected=[],
                skipped=[],
            )

        # 1. 현재 계좌 상태 조회
        balance = self._brokerage.get_balance()
        positions = self._brokerage.get_positions()
        positions_map = {p.symbol: p for p in positions}

        total_equity = balance.total_equity if balance.total_equity > 0 else order.total_capital

        logger.info(
            f"계좌 상태: 총평가 {total_equity:,.0f}원, "
            f"가용현금 {balance.available_cash:,.0f}원, "
            f"보유종목 {len(positions)}개"
        )

        # 2. 각 종목별 PlannedTrade 생성
        planned_trades = self._compute_trades(
            order=order,
            total_equity=total_equity,
            positions_map=positions_map,
        )

        if not planned_trades:
            logger.info("변경 사항 없음 — 모든 종목 HOLD")
            return ExecutionReport(planned=[])

        # 3. dry_run이면 여기서 종료
        if dry_run:
            logger.info(f"Dry run: {len(planned_trades)}건 계획됨")
            return ExecutionReport(planned=planned_trades)

        # 4. 매도 먼저, 매수 나중 (현금 확보 원칙)
        sells = [t for t in planned_trades if t.side == OrderSide.SELL]
        buys = [t for t in planned_trades if t.side == OrderSide.BUY]

        executed: List[Order] = []
        rejected: List[tuple] = []
        skipped: List[tuple] = []
        total_commission = 0.0

        # 매도 실행
        for trade in sells:
            result = self._submit_single(trade)
            if result is None:
                rejected.append((trade, "주문 제출 실패"))
            else:
                executed.append(result)
                total_commission += result.commission or 0.0

        # 매수 실행 — 잔고 재확인
        if buys:
            refreshed_balance = self._brokerage.get_balance()
            available = refreshed_balance.available_cash

            for trade in buys:
                if trade.estimated_amount > available:
                    skipped.append((trade, f"현금 부족: 필요 {trade.estimated_amount:,.0f}원 > 가용 {available:,.0f}원"))
                    continue

                result = self._submit_single(trade)
                if result is None:
                    rejected.append((trade, "주문 제출 실패"))
                else:
                    executed.append(result)
                    total_commission += result.commission or 0.0
                    available -= trade.estimated_amount

        return ExecutionReport(
            planned=planned_trades,
            executed=executed,
            rejected=rejected,
            skipped=skipped,
            total_commission=total_commission,
            total_slippage_estimate=sum(
                t.estimated_cost.slippage for t in planned_trades
            ),
        )

    def plan(self, order: PortfolioOrder) -> ExecutionReport:
        """주문 계획만 생성 (실행 없음). dry_run 단축."""
        return self.execute(order, dry_run=True)

    def _compute_trades(
        self,
        order: PortfolioOrder,
        total_equity: float,
        positions_map: Dict[str, Position],
    ) -> List[PlannedTrade]:
        """StockAllocation → PlannedTrade 변환"""
        trades: List[PlannedTrade] = []

        for alloc in order.allocations:
            if alloc.action == OrderAction.HOLD:
                continue

            current_price = self._get_price_safe(alloc.ticker)
            if current_price <= 0:
                logger.warning(f"{alloc.name}({alloc.ticker}): 현재가 조회 실패 — 스킵")
                continue

            current_pos = positions_map.get(alloc.ticker)
            current_qty = current_pos.quantity if current_pos else 0
            current_weight = (
                (current_qty * current_price) / total_equity
                if total_equity > 0 else 0.0
            )

            target_qty = math.floor(
                alloc.target_weight * total_equity / current_price
            )

            qty_diff = target_qty - current_qty

            if qty_diff == 0:
                continue

            side = OrderSide.BUY if qty_diff > 0 else OrderSide.SELL
            trade_qty = abs(qty_diff)
            trade_amount = trade_qty * current_price

            # 최소 거래금액 체크
            if trade_amount < self._min_trade_amount:
                logger.debug(
                    f"{alloc.name}: 거래금액 {trade_amount:,.0f}원 < "
                    f"최소 {self._min_trade_amount:,.0f}원 — 스킵"
                )
                continue

            # 거래비용 추정
            market = alloc.market
            cost = self._estimate_cost(
                price=current_price,
                quantity=trade_qty,
                side=side,
                market=market,
            )

            # 거래 사유 결정
            reason = self._determine_reason(
                alloc=alloc,
                current_qty=current_qty,
                target_qty=target_qty,
            )

            trades.append(PlannedTrade(
                symbol=alloc.ticker,
                name=alloc.name,
                side=side,
                quantity=trade_qty,
                estimated_price=current_price,
                estimated_cost=cost,
                reason=reason,
                target_weight=alloc.target_weight,
                current_weight=current_weight,
            ))

        return trades

    def _estimate_cost(
        self,
        price: float,
        quantity: int,
        side: OrderSide,
        market: Market,
    ) -> TransactionCostEstimate:
        """거래비용 사전 추정"""
        amount = price * quantity
        commission = amount * self._cost_model.fees.broker_commission

        tax = 0.0
        if side == OrderSide.SELL:
            tax = amount * self._cost_model.sell_tax_rate(market)

        slippage = amount * (self._cost_model.slippage_bps / 10_000)

        return TransactionCostEstimate(
            commission=commission,
            tax=tax,
            slippage=slippage,
        )

    def _determine_reason(
        self,
        alloc: StockAllocation,
        current_qty: int,
        target_qty: int,
    ) -> TradeReason:
        """거래 사유 결정"""
        if current_qty == 0 and target_qty > 0:
            return TradeReason.NEW_ENTRY
        if target_qty == 0 and current_qty > 0:
            return TradeReason.EXIT
        if target_qty < current_qty:
            return TradeReason.REDUCE
        return TradeReason.REBALANCE

    def _get_price_safe(self, symbol: str) -> float:
        """현재가 조회 (실패 시 0 반환)"""
        try:
            return self._price_provider.get_current_price(symbol)
        except Exception as e:
            logger.error(f"현재가 조회 실패 {symbol}: {e}")
            return 0.0

    def _submit_single(self, trade: PlannedTrade) -> Optional[Order]:
        """단일 주문 제출"""
        try:
            order = self._brokerage.submit_order(
                symbol=trade.symbol,
                side=trade.side,
                quantity=trade.quantity,
                order_type=OrderType.MARKET,
                price=None,
            )
            logger.info(
                f"주문 제출: {trade.summary_line()} → "
                f"주문번호 {order.id}"
            )
            return order
        except Exception as e:
            logger.error(f"주문 제출 실패 {trade.symbol}: {e}")
            return None
