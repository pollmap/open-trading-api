"""체결 추적 및 대사(Reconciliation) 모듈

KIS WebSocket 체결 통보와 계획된 주문을 대사하여
슬리피지, 체결률, 비용을 추적한다.

Flow:
    ExecutionReport (계획+실행 주문)
      ↓
    FillTracker.register()
      ↓
    WebSocket FillNotice → FillTracker.on_fill()
      ↓
    FillTracker.reconcile() → ReconciliationReport
      ↓
    TradeRecord 리스트 → ReviewEngine
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from kis_backtest.execution.models import ExecutionReport, PlannedTrade
from kis_backtest.models import Order, OrderSide
from kis_backtest.portfolio.review_engine import TradeRecord
from kis_backtest.providers.kis.websocket import FillNotice
from kis_backtest.strategies.risk.cost_model import KoreaTransactionCostModel, Market

logger = logging.getLogger(__name__)

# KIS WebSocket side 코드 → OrderSide 매핑
_KIS_SIDE_MAP: Dict[str, OrderSide] = {
    "01": OrderSide.SELL,
    "02": OrderSide.BUY,
}


@dataclass
class TrackedOrder:
    """추적 중인 주문

    register()에서 생성, on_fill()에서 갱신.
    """

    order_id: str
    symbol: str
    name: str
    side: OrderSide
    planned_qty: int
    planned_price: float
    filled_qty: int = 0
    filled_price: float = 0.0
    status: str = "pending"  # pending, partial, filled, timeout, rejected
    fills: List[dict] = field(default_factory=list)
    submitted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass(frozen=True)
class ReconciliationReport:
    """체결 대사 보고서

    FillTracker.reconcile()의 반환값.
    trade_records를 ReviewEngine에 전달하여 주간 복기에 활용.
    """

    timestamp: datetime
    total_orders: int
    filled_orders: int
    partial_orders: int
    rejected_orders: int
    timeout_orders: int
    total_slippage: float
    avg_slippage_bps: float
    trade_records: Tuple[TradeRecord, ...]

    def summary(self) -> str:
        """대사 결과 요약 문자열"""
        lines = [
            "=== 체결 대사 보고서 ===",
            f"시간: {self.timestamp:%Y-%m-%d %H:%M:%S}",
            f"총 주문: {self.total_orders}건",
            f"  완전체결: {self.filled_orders}건",
            f"  부분체결: {self.partial_orders}건",
            f"  거부:     {self.rejected_orders}건",
            f"  타임아웃: {self.timeout_orders}건",
            f"총 슬리피지: {self.total_slippage:,.0f}원",
            f"평균 슬리피지: {self.avg_slippage_bps:.1f}bps",
        ]
        if self.trade_records:
            lines.append("")
            lines.append("--- 거래 기록 ---")
            for tr in self.trade_records:
                lines.append(
                    f"  {tr.date} {tr.action} {tr.ticker} "
                    f"{tr.quantity}주 × {tr.price:,.0f}원 "
                    f"= {tr.amount:,.0f}원"
                )
        return "\n".join(lines)


class FillTracker:
    """체결 추적기

    ExecutionReport의 주문을 등록하고, WebSocket FillNotice로
    체결 상태를 갱신한 뒤, 대사 보고서를 생성한다.

    Usage:
        tracker = FillTracker(timeout_seconds=300.0)

        # 1) 주문 실행 후 등록
        tracker.register(execution_report)

        # 2) WebSocket 콜백에서 호출
        ws.subscribe_fills(tracker.on_fill)

        # 3) 타임아웃 체크
        timed_out = tracker.check_timeouts()

        # 4) 대사 보고서 생성
        report = tracker.reconcile()
        print(report.summary())
    """

    def __init__(self, timeout_seconds: float = 300.0) -> None:
        """초기화

        Args:
            timeout_seconds: 미체결 타임아웃 (초). 기본 5분.
        """
        self._tracked: Dict[str, TrackedOrder] = {}
        self._timeout = timeout_seconds
        self._cost_model = KoreaTransactionCostModel()

    def register(self, report: ExecutionReport) -> None:
        """ExecutionReport에서 실행된 주문을 추적 대상으로 등록

        Order.id를 키로 TrackedOrder 생성.
        PlannedTrade는 symbol+side 조합으로 매칭.

        Args:
            report: LiveOrderExecutor의 실행 결과
        """
        planned_map: Dict[Tuple[str, OrderSide], PlannedTrade] = {}
        for trade in report.planned:
            key = (trade.symbol, trade.side)
            planned_map[key] = trade

        now = datetime.now()
        for order in report.executed:
            match_key = (order.symbol, order.side)
            planned = planned_map.get(match_key)

            name = planned.name if planned else order.symbol
            planned_price = planned.estimated_price if planned else 0.0
            planned_qty = planned.quantity if planned else order.quantity

            tracked = TrackedOrder(
                order_id=order.id,
                symbol=order.symbol,
                name=name,
                side=order.side,
                planned_qty=planned_qty,
                planned_price=planned_price,
                filled_qty=order.filled_quantity,
                filled_price=order.average_price,
                status=_derive_status(order.filled_quantity, planned_qty),
                submitted_at=order.created_at,
            )

            if tracked.status == "filled":
                tracked.completed_at = now

            self._tracked[order.id] = tracked
            logger.info(
                "주문 등록: %s %s %s %d주 @%,.0f",
                order.id,
                name,
                order.side.value,
                planned_qty,
                planned_price,
            )

    def on_fill(self, notice: FillNotice) -> None:
        """WebSocket 체결 통보 수신 시 호출

        TrackedOrder의 체결 수량/가격을 갱신하고,
        planned_qty 이상 체결되면 'filled'로 표시.

        Args:
            notice: KIS WebSocket FillNotice
        """
        if notice.is_rejected:
            tracked = self._tracked.get(notice.order_no)
            if tracked is not None:
                tracked.status = "rejected"
                tracked.completed_at = datetime.now()
                logger.warning("주문 거부: %s %s", notice.order_no, notice.symbol)
            return

        if not notice.is_fill:
            return

        tracked = self._tracked.get(notice.order_no)
        if tracked is None:
            logger.debug(
                "미등록 주문 체결 통보 무시: order_no=%s symbol=%s",
                notice.order_no,
                notice.symbol,
            )
            return

        fill_event = {
            "qty": notice.fill_qty,
            "price": notice.fill_price,
            "time": notice.fill_time,
        }
        tracked.fills.append(fill_event)

        prev_filled = tracked.filled_qty
        tracked.filled_qty = prev_filled + notice.fill_qty

        # 가중평균 체결가 갱신
        if tracked.filled_qty > 0:
            tracked.filled_price = (
                prev_filled * tracked.filled_price
                + notice.fill_qty * notice.fill_price
            ) / tracked.filled_qty

        if tracked.filled_qty >= tracked.planned_qty:
            tracked.status = "filled"
            tracked.completed_at = datetime.now()
            logger.info(
                "완전체결: %s %s %d주 @%,.0f (계획 %,.0f)",
                tracked.order_id,
                tracked.name,
                tracked.filled_qty,
                tracked.filled_price,
                tracked.planned_price,
            )
        else:
            tracked.status = "partial"
            logger.info(
                "부분체결: %s %s %d/%d주 @%,.0f",
                tracked.order_id,
                tracked.name,
                tracked.filled_qty,
                tracked.planned_qty,
                tracked.filled_price,
            )

    def check_timeouts(self, now: Optional[datetime] = None) -> List[TrackedOrder]:
        """타임아웃된 주문 확인

        submitted_at으로부터 timeout_seconds 초과 시
        'timeout' 상태로 변경.

        Args:
            now: 현재 시각. None이면 datetime.now() 사용.

        Returns:
            타임아웃된 TrackedOrder 리스트
        """
        current = now or datetime.now()
        timed_out: List[TrackedOrder] = []

        for tracked in self._tracked.values():
            if tracked.status not in ("pending", "partial"):
                continue
            if tracked.submitted_at is None:
                continue
            elapsed = (current - tracked.submitted_at).total_seconds()
            if elapsed > self._timeout:
                tracked.status = "timeout"
                tracked.completed_at = current
                timed_out.append(tracked)
                logger.warning(
                    "타임아웃: %s %s %.0f초 경과 (체결 %d/%d)",
                    tracked.order_id,
                    tracked.name,
                    elapsed,
                    tracked.filled_qty,
                    tracked.planned_qty,
                )

        return timed_out

    def reconcile(self) -> ReconciliationReport:
        """체결 대사 보고서 생성

        슬리피지 계산: (체결가 - 계획가) / 계획가 * 10000 (bps)
        TrackedOrder를 TradeRecord로 변환하여 ReviewEngine에 전달 가능.

        Returns:
            ReconciliationReport (frozen)
        """
        filled_count = 0
        partial_count = 0
        rejected_count = 0
        timeout_count = 0
        total_slippage = 0.0
        slippage_bps_list: List[float] = []
        records: List[TradeRecord] = []

        for tracked in self._tracked.values():
            if tracked.status == "filled":
                filled_count += 1
            elif tracked.status == "partial":
                partial_count += 1
            elif tracked.status == "rejected":
                rejected_count += 1
            elif tracked.status == "timeout":
                timeout_count += 1

            if tracked.filled_qty <= 0:
                continue

            # 슬리피지 계산
            price_diff = tracked.filled_price - tracked.planned_price
            order_slippage = price_diff * tracked.filled_qty
            total_slippage += order_slippage

            if tracked.planned_price > 0:
                bps = (price_diff / tracked.planned_price) * 10_000
                slippage_bps_list.append(bps)

            # TradeRecord 변환
            record = _to_trade_record(tracked, self._cost_model)
            records.append(record)

        avg_bps = (
            sum(slippage_bps_list) / len(slippage_bps_list)
            if slippage_bps_list
            else 0.0
        )

        report = ReconciliationReport(
            timestamp=datetime.now(),
            total_orders=len(self._tracked),
            filled_orders=filled_count,
            partial_orders=partial_count,
            rejected_orders=rejected_count,
            timeout_orders=timeout_count,
            total_slippage=total_slippage,
            avg_slippage_bps=avg_bps,
            trade_records=tuple(records),
        )

        logger.info(
            "대사 완료: %d건 (체결 %d / 부분 %d / 거부 %d / 타임아웃 %d) 슬리피지 %,.0f원 (%.1fbps)",
            report.total_orders,
            filled_count,
            partial_count,
            rejected_count,
            timeout_count,
            total_slippage,
            avg_bps,
        )
        return report

    def is_all_filled(self) -> bool:
        """모든 추적 주문이 최종 상태(filled/rejected/timeout)인지 확인"""
        if not self._tracked:
            return True
        return all(
            t.status in ("filled", "rejected", "timeout")
            for t in self._tracked.values()
        )

    @property
    def pending_count(self) -> int:
        """미완료(pending/partial) 주문 수"""
        return sum(
            1
            for t in self._tracked.values()
            if t.status in ("pending", "partial")
        )

    @property
    def tracked_orders(self) -> List[TrackedOrder]:
        """추적 중인 전체 주문 목록"""
        return list(self._tracked.values())


# ============================================
# 내부 유틸리티
# ============================================


def _derive_status(filled_qty: int, planned_qty: int) -> str:
    """체결 수량으로 상태 결정"""
    if filled_qty <= 0:
        return "pending"
    if filled_qty >= planned_qty:
        return "filled"
    return "partial"


def _to_trade_record(
    tracked: TrackedOrder,
    cost_model: KoreaTransactionCostModel,
) -> TradeRecord:
    """TrackedOrder → TradeRecord 변환

    KoreaTransactionCostModel로 수수료/세금 계산.
    ReviewEngine의 주간 복기에 사용.

    Args:
        tracked: 체결 완료된 TrackedOrder
        cost_model: 거래비용 모델

    Returns:
        TradeRecord
    """
    is_sell = tracked.side == OrderSide.SELL
    action = "SELL" if is_sell else "BUY"
    amount = tracked.filled_qty * tracked.filled_price

    cost = cost_model.trade_cost(
        value=amount,
        market=Market.KOSPI,
        is_sell=is_sell,
    )

    slippage = 0.0
    if tracked.planned_price > 0:
        slippage = (
            (tracked.filled_price - tracked.planned_price)
            * tracked.filled_qty
        )

    completed = tracked.completed_at or datetime.now()
    date_str = completed.strftime("%Y-%m-%d")

    return TradeRecord(
        date=date_str,
        ticker=tracked.symbol,
        action=action,
        quantity=tracked.filled_qty,
        price=tracked.filled_price,
        amount=amount,
        commission=cost.broker_fee,
        tax=cost.tax,
        slippage=slippage,
    )
