"""Phase 3 모니터링 테스트 — alerts, fill_tracker, live_monitor

시나리오:
    1. AlertSystem — 콘솔 로깅, Discord 웹훅 플래그, 편의 메서드
    2. FillTracker — 주문 등록, 체결 추적, 타임아웃, 대사 보고서
    3. LiveMonitor — 포지션 스냅샷, 가격 갱신, 드로다운 감지, 킬 스위치
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from kis_backtest.models import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from kis_backtest.execution.alerts import AlertLevel, AlertSystem
from kis_backtest.execution.fill_tracker import FillTracker, TrackedOrder
from kis_backtest.execution.kill_switch import KillSwitch
from kis_backtest.execution.live_monitor import (
    LiveMonitor,
    MonitorState,
    PositionSnapshot,
)
from kis_backtest.execution.models import (
    ExecutionReport,
    PlannedTrade,
    TradeReason,
    TransactionCostEstimate,
)
from kis_backtest.providers.kis.websocket import FillNotice, RealtimePrice


# ─── Mock 구현 ────────────────────────────────


class MockBrokerage:
    """테스트용 브로커리지"""

    def __init__(
        self,
        balance: AccountBalance,
        positions: List[Position],
    ):
        self._balance = balance
        self._positions = positions

    def get_balance(self) -> AccountBalance:
        return self._balance

    def get_positions(self) -> List[Position]:
        return self._positions


# ─── 헬퍼 팩토리 ────────────────────────────────


def make_balance(
    equity: float = 5_000_000,
    cash: float = 1_000_000,
    pnl: float = 0.0,
) -> AccountBalance:
    """테스트용 잔고 생성"""
    return AccountBalance(
        total_cash=cash,
        available_cash=cash,
        total_equity=equity,
        total_pnl=pnl,
        total_pnl_percent=(pnl / equity * 100 if equity else 0),
    )


def make_position(
    symbol: str = "005930",
    qty: int = 10,
    avg: float = 50000,
    cur: float = 55000,
    name: str = "삼성전자",
) -> Position:
    """테스트용 포지션 생성"""
    pnl = (cur - avg) * qty
    return Position(
        symbol=symbol,
        quantity=qty,
        average_price=avg,
        current_price=cur,
        unrealized_pnl=pnl,
        unrealized_pnl_percent=((cur - avg) / avg * 100),
        name=name,
    )


def make_order(
    id: str = "ORD001",
    symbol: str = "005930",
    side: OrderSide = OrderSide.BUY,
    qty: int = 10,
    filled: int = 10,
    avg_price: float = 50000,
) -> Order:
    """테스트용 주문 생성"""
    return Order(
        id=id,
        symbol=symbol,
        side=side,
        order_type=OrderType.MARKET,
        quantity=qty,
        filled_quantity=filled,
        average_price=avg_price,
        status=OrderStatus.FILLED,
        created_at=datetime.now(),
        updated_at=datetime.now(),
    )


def make_fill_notice(
    order_no: str = "ORD001",
    symbol: str = "005930",
    side: str = "02",
    fill_qty: int = 10,
    fill_price: int = 50500,
    is_fill: bool = True,
    is_rejected: bool = False,
) -> FillNotice:
    """테스트용 체결 통보 생성"""
    return FillNotice(
        customer_id="test",
        account_no="1234",
        order_no=order_no,
        order_qty=fill_qty,
        side=side,
        symbol=symbol,
        fill_qty=fill_qty,
        fill_price=fill_price,
        fill_time="100000",
        is_fill=is_fill,
        is_rejected=is_rejected,
    )


def make_realtime_price(
    symbol: str = "005930",
    price: int = 55000,
) -> RealtimePrice:
    """테스트용 실시간 체결가 생성"""
    return RealtimePrice(
        symbol=symbol,
        time="100000",
        price=price,
        change_sign="2",
        change=5000,
        change_rate=10.0,
        open_price=50000,
        high_price=56000,
        low_price=49000,
        volume=100,
        total_volume=100000,
        ask_price=price + 100,
        bid_price=price - 100,
    )


def make_planned_trade(
    symbol: str = "005930",
    name: str = "삼성전자",
    side: OrderSide = OrderSide.BUY,
    qty: int = 10,
    price: float = 50000.0,
) -> PlannedTrade:
    """테스트용 계획 거래 생성"""
    cost = TransactionCostEstimate(commission=100, tax=0, slippage=50)
    return PlannedTrade(
        symbol=symbol,
        name=name,
        side=side,
        quantity=qty,
        estimated_price=price,
        estimated_cost=cost,
        reason=TradeReason.NEW_ENTRY,
        target_weight=0.3,
    )


def make_execution_report(
    planned: List[PlannedTrade],
    executed: List[Order],
) -> ExecutionReport:
    """테스트용 실행 리포트 생성"""
    return ExecutionReport(planned=planned, executed=executed)


# ============================================
# AlertSystem 테스트
# ============================================


class TestAlertSystem:
    """AlertSystem 시나리오: 콘솔 로깅, Discord 플래그, 편의 메서드"""

    def test_console_only_no_discord(self):
        """Discord 웹훅 미설정 → discord_enabled False"""
        alerts = AlertSystem()
        assert alerts.discord_enabled is False

    def test_discord_enabled_with_url(self):
        """Discord 웹훅 URL 설정 → discord_enabled True"""
        alerts = AlertSystem(discord_webhook_url="https://discord.com/api/webhooks/test")
        assert alerts.discord_enabled is True

    def test_info_level_logging(self, caplog):
        """info() → INFO 레벨 로그 출력"""
        alerts = AlertSystem()
        with caplog.at_level(logging.INFO, logger="kis_backtest.execution.alerts"):
            alerts.info("테스트 제목", "테스트 메시지")

        assert any("[INFO]" in r.message for r in caplog.records)
        assert any("테스트 제목" in r.message for r in caplog.records)

    def test_warning_level_logging(self, caplog):
        """warning() → WARNING 레벨 로그 출력"""
        alerts = AlertSystem()
        with caplog.at_level(logging.WARNING, logger="kis_backtest.execution.alerts"):
            alerts.warning("경고 제목", "경고 메시지")

        assert any("[WARNING]" in r.message for r in caplog.records)
        assert any("경고 제목" in r.message for r in caplog.records)

    def test_critical_level_logging(self, caplog):
        """critical() → CRITICAL 레벨 로그 출력"""
        alerts = AlertSystem()
        with caplog.at_level(logging.CRITICAL, logger="kis_backtest.execution.alerts"):
            alerts.critical("심각 제목", "심각 메시지")

        assert any("[CRITICAL]" in r.message for r in caplog.records)
        assert any("심각 제목" in r.message for r in caplog.records)

    def test_kill_level_logging(self, caplog):
        """kill() → CRITICAL 레벨(KILL 접두사) 로그 출력"""
        alerts = AlertSystem()
        with caplog.at_level(logging.CRITICAL, logger="kis_backtest.execution.alerts"):
            alerts.kill("킬 제목", "킬 메시지")

        assert any("[KILL]" in r.message for r in caplog.records)
        assert any("킬 제목" in r.message for r in caplog.records)

    def test_convenience_order_executed(self, caplog):
        """order_executed() → INFO 레벨, 금액 포맷 포함"""
        alerts = AlertSystem()
        with caplog.at_level(logging.INFO, logger="kis_backtest.execution.alerts"):
            alerts.order_executed("삼성전자 10주 매수", 500_000)

        assert any("주문 체결" in r.message for r in caplog.records)
        assert any("500,000" in r.message for r in caplog.records)

    def test_convenience_dd_warning(self, caplog):
        """dd_warning() → CRITICAL 레벨, DD 수치 포함"""
        alerts = AlertSystem()
        with caplog.at_level(logging.CRITICAL, logger="kis_backtest.execution.alerts"):
            alerts.dd_warning(current_dd=-7.5, threshold=-8.0)

        assert any("DD 경고" in r.message for r in caplog.records)
        assert any("-7.5%" in r.message for r in caplog.records)

    def test_convenience_kill_switch(self, caplog):
        """kill_switch_activated() → KILL 레벨, 사유 포함"""
        alerts = AlertSystem()
        with caplog.at_level(logging.CRITICAL, logger="kis_backtest.execution.alerts"):
            alerts.kill_switch_activated("DD -10% 초과")

        assert any("[KILL]" in r.message for r in caplog.records)
        assert any("DD -10% 초과" in r.message for r in caplog.records)

    def test_alert_with_data_dict(self, caplog):
        """data dict 전달 시 로그에 포함"""
        alerts = AlertSystem()
        data = {"key": "value", "count": 42}
        with caplog.at_level(logging.INFO, logger="kis_backtest.execution.alerts"):
            alerts.alert(AlertLevel.INFO, "데이터 테스트", "메시지", data=data)

        assert any("data=" in r.message for r in caplog.records)
        assert any("key" in r.message for r in caplog.records)


# ============================================
# FillTracker 테스트
# ============================================


class TestFillTrackerRegister:
    """FillTracker.register() — ExecutionReport → TrackedOrder 변환"""

    def test_register_from_execution_report(self):
        """실행된 주문이 TrackedOrder로 등록"""
        tracker = FillTracker(timeout_seconds=300.0)

        planned = [make_planned_trade()]
        executed = [make_order(filled=0, avg_price=0)]
        report = make_execution_report(planned, executed)

        tracker.register(report)

        assert len(tracker.tracked_orders) == 1
        tracked = tracker.tracked_orders[0]
        assert tracked.order_id == "ORD001"
        assert tracked.symbol == "005930"
        assert tracked.side == OrderSide.BUY

    def test_register_empty_report(self):
        """실행된 주문 없음 → 빈 tracker"""
        tracker = FillTracker()

        report = make_execution_report(planned=[], executed=[])
        tracker.register(report)

        assert len(tracker.tracked_orders) == 0
        assert tracker.is_all_filled() is True

    def test_register_matches_planned_by_symbol_side(self):
        """PlannedTrade의 name/price가 TrackedOrder에 반영"""
        tracker = FillTracker()

        planned = [make_planned_trade(name="SK하이닉스", symbol="000660", price=180000.0)]
        executed = [make_order(id="ORD002", symbol="000660", filled=0, avg_price=0)]
        report = make_execution_report(planned, executed)

        tracker.register(report)

        tracked = tracker.tracked_orders[0]
        assert tracked.name == "SK하이닉스"
        assert tracked.planned_price == 180000.0


class TestFillTrackerOnFill:
    """FillTracker.on_fill() — WebSocket 체결 통보 처리"""

    def _register_one(self) -> FillTracker:
        """등록된 주문 1건이 있는 tracker 반환"""
        tracker = FillTracker()
        planned = [make_planned_trade(qty=10, price=50000.0)]
        executed = [make_order(id="ORD001", filled=0, avg_price=0)]
        tracker.register(make_execution_report(planned, executed))
        return tracker

    def test_full_fill(self):
        """단일 체결로 planned_qty 완전 체결 → status 'filled'"""
        tracker = self._register_one()
        notice = make_fill_notice(order_no="ORD001", fill_qty=10, fill_price=50500)

        tracker.on_fill(notice)

        tracked = tracker.tracked_orders[0]
        assert tracked.status == "filled"
        assert tracked.filled_qty == 10
        assert tracked.filled_price == 50500

    def test_partial_fill(self):
        """부분 체결 → status 'partial'"""
        tracker = self._register_one()
        notice = make_fill_notice(order_no="ORD001", fill_qty=5, fill_price=50200)

        tracker.on_fill(notice)

        tracked = tracker.tracked_orders[0]
        assert tracked.status == "partial"
        assert tracked.filled_qty == 5

    def test_multiple_partial_fills(self):
        """복수 부분 체결 → 가중평균 체결가 계산"""
        tracker = self._register_one()

        # 1차 체결: 6주 × 50200원
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=6, fill_price=50200))
        # 2차 체결: 4주 × 50800원
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=4, fill_price=50800))

        tracked = tracker.tracked_orders[0]
        assert tracked.status == "filled"
        assert tracked.filled_qty == 10

        # 가중평균: (6*50200 + 4*50800) / 10 = 50440
        expected_avg = (6 * 50200 + 4 * 50800) / 10
        assert tracked.filled_price == pytest.approx(expected_avg, rel=1e-6)

    def test_rejected_fill(self):
        """is_rejected=True → status 'rejected'"""
        tracker = self._register_one()
        notice = make_fill_notice(
            order_no="ORD001",
            fill_qty=0,
            fill_price=0,
            is_fill=False,
            is_rejected=True,
        )

        tracker.on_fill(notice)

        tracked = tracker.tracked_orders[0]
        assert tracked.status == "rejected"
        assert tracked.completed_at is not None

    def test_unregistered_order_ignored(self):
        """미등록 order_no → 에러 없이 무시"""
        tracker = self._register_one()
        notice = make_fill_notice(order_no="UNKNOWN_999", fill_qty=10, fill_price=50000)

        # 예외 없이 정상 종료
        tracker.on_fill(notice)

        # 기존 주문은 변경 없음
        tracked = tracker.tracked_orders[0]
        assert tracked.status == "pending"

    def test_non_fill_notice_ignored(self):
        """is_fill=False (접수 통보) → 체결 갱신 없음"""
        tracker = self._register_one()
        notice = make_fill_notice(
            order_no="ORD001",
            fill_qty=10,
            fill_price=50000,
            is_fill=False,
            is_rejected=False,
        )

        tracker.on_fill(notice)

        tracked = tracker.tracked_orders[0]
        assert tracked.status == "pending"
        assert tracked.filled_qty == 0


class TestFillTrackerTimeout:
    """FillTracker.check_timeouts() — 미체결 타임아웃"""

    def test_timeout_pending_order(self):
        """timeout 초과 → status 'timeout'"""
        tracker = FillTracker(timeout_seconds=60.0)

        planned = [make_planned_trade()]
        order = make_order(id="ORD001", filled=0, avg_price=0)
        tracker.register(make_execution_report(planned, [order]))

        # 90초 후 시점에서 타임아웃 체크
        future = datetime.now() + timedelta(seconds=90)
        timed_out = tracker.check_timeouts(now=future)

        assert len(timed_out) == 1
        assert timed_out[0].status == "timeout"
        assert timed_out[0].order_id == "ORD001"

    def test_no_timeout_if_filled(self):
        """체결된 주문은 타임아웃 대상 아님"""
        tracker = FillTracker(timeout_seconds=60.0)

        planned = [make_planned_trade()]
        order = make_order(id="ORD001", filled=0, avg_price=0)
        tracker.register(make_execution_report(planned, [order]))

        # 체결 처리
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=10, fill_price=50500))

        # 2시간 후 타임아웃 체크
        future = datetime.now() + timedelta(hours=2)
        timed_out = tracker.check_timeouts(now=future)

        assert len(timed_out) == 0


class TestFillTrackerReconcile:
    """FillTracker.reconcile() — 대사 보고서 생성"""

    def test_reconcile_slippage_calculation(self):
        """슬리피지 = (체결가 - 계획가) × 수량"""
        tracker = FillTracker()

        planned = [make_planned_trade(qty=10, price=50000.0)]
        executed = [make_order(id="ORD001", filled=0, avg_price=0)]
        tracker.register(make_execution_report(planned, executed))

        # 50500원에 체결 → 슬리피지 = (50500-50000)*10 = 5000
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=10, fill_price=50500))

        report = tracker.reconcile()
        assert report.total_slippage == pytest.approx(5000.0)
        assert report.filled_orders == 1

        # bps: (50500-50000)/50000 * 10000 = 100bps
        assert report.avg_slippage_bps == pytest.approx(100.0, rel=1e-3)

    def test_reconcile_generates_trade_records(self):
        """TradeRecord 리스트가 ReviewEngine 용으로 생성"""
        tracker = FillTracker()

        planned = [make_planned_trade(qty=10, price=50000.0)]
        executed = [make_order(id="ORD001", filled=0, avg_price=0)]
        tracker.register(make_execution_report(planned, executed))
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=10, fill_price=50500))

        report = tracker.reconcile()
        assert len(report.trade_records) == 1

        record = report.trade_records[0]
        assert record.ticker == "005930"
        assert record.action == "BUY"
        assert record.quantity == 10
        assert record.price == pytest.approx(50500.0)

    def test_reconcile_empty(self):
        """빈 tracker → 모든 카운트 0"""
        tracker = FillTracker()
        report = tracker.reconcile()

        assert report.total_orders == 0
        assert report.filled_orders == 0
        assert report.partial_orders == 0
        assert report.rejected_orders == 0
        assert report.timeout_orders == 0
        assert report.total_slippage == 0.0
        assert report.avg_slippage_bps == 0.0
        assert len(report.trade_records) == 0

    def test_is_all_filled_true(self):
        """모든 주문이 filled/rejected/timeout → True"""
        tracker = FillTracker(timeout_seconds=10.0)

        planned = [
            make_planned_trade(symbol="005930", qty=10, price=50000.0),
            make_planned_trade(symbol="000660", qty=5, price=180000.0),
        ]
        executed = [
            make_order(id="ORD001", symbol="005930", filled=0, avg_price=0),
            make_order(id="ORD002", symbol="000660", filled=0, avg_price=0),
        ]
        tracker.register(make_execution_report(planned, executed))

        # ORD001: 체결
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=10, fill_price=50500))
        # ORD002: 거부
        tracker.on_fill(make_fill_notice(
            order_no="ORD002", symbol="000660",
            fill_qty=0, fill_price=0, is_fill=False, is_rejected=True,
        ))

        assert tracker.is_all_filled() is True

    def test_pending_count(self):
        """pending/partial 주문 수 카운트"""
        tracker = FillTracker()

        planned = [
            make_planned_trade(symbol="005930", qty=10, price=50000.0),
            make_planned_trade(symbol="000660", qty=5, price=180000.0),
        ]
        executed = [
            make_order(id="ORD001", symbol="005930", filled=0, avg_price=0),
            make_order(id="ORD002", symbol="000660", filled=0, avg_price=0),
        ]
        tracker.register(make_execution_report(planned, executed))

        assert tracker.pending_count == 2

        # ORD001만 부분 체결
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=3, fill_price=50200))
        assert tracker.pending_count == 2  # partial도 미완료

        # ORD001 완전 체결
        tracker.on_fill(make_fill_notice(order_no="ORD001", fill_qty=7, fill_price=50400))
        assert tracker.pending_count == 1  # ORD002만 남음


# ============================================
# LiveMonitor 테스트
# ============================================


class TestPositionSnapshot:
    """PositionSnapshot.from_position() — Position → 스냅샷 변환"""

    def test_from_position(self):
        """Position 모델에서 스냅샷 정확히 변환"""
        pos = make_position(symbol="005930", qty=10, avg=50000, cur=55000, name="삼성전자")
        now = datetime(2026, 4, 7, 10, 0, 0)
        snap = PositionSnapshot.from_position(pos, now=now)

        assert snap.symbol == "005930"
        assert snap.name == "삼성전자"
        assert snap.quantity == 10
        assert snap.avg_price == 50000
        assert snap.current_price == 55000
        assert snap.unrealized_pnl == 50000  # (55000-50000)*10
        assert snap.unrealized_pnl_pct == pytest.approx(10.0)
        assert snap.last_updated == now


class TestLiveMonitorInitialize:
    """LiveMonitor.initialize() — REST 조회 후 초기 상태 설정"""

    def test_initialize_sets_state(self, tmp_path):
        """포지션/잔고 조회 → MonitorState 구성"""
        brokerage = MockBrokerage(
            balance=make_balance(equity=10_000_000, cash=2_000_000, pnl=500_000),
            positions=[
                make_position("005930", qty=50, avg=65000, cur=70000, name="삼성전자"),
                make_position("000660", qty=20, avg=170000, cur=180000, name="SK하이닉스"),
            ],
        )
        kill_switch = KillSwitch(lock_path=tmp_path / "kill.lock")
        monitor = LiveMonitor(brokerage, kill_switch)

        state = monitor.initialize()

        assert len(state.positions) == 2
        assert "005930" in state.positions
        assert "000660" in state.positions
        assert state.total_equity == 10_000_000
        assert state.peak_equity == 10_000_000
        assert state.current_dd == 0.0

    def test_initialize_empty_positions(self, tmp_path):
        """포지션 없는 경우 → 빈 상태"""
        brokerage = MockBrokerage(
            balance=make_balance(equity=5_000_000, cash=5_000_000, pnl=0),
            positions=[],
        )
        kill_switch = KillSwitch(lock_path=tmp_path / "kill.lock")
        monitor = LiveMonitor(brokerage, kill_switch)

        state = monitor.initialize()

        assert len(state.positions) == 0
        assert state.total_equity == 5_000_000


class TestLiveMonitorOnPrice:
    """LiveMonitor.on_price() — 실시간 가격 갱신"""

    def _setup_monitor(self, tmp_path, positions=None, equity=10_000_000):
        """초기화된 LiveMonitor 반환"""
        if positions is None:
            positions = [
                make_position("005930", qty=100, avg=50000, cur=50000, name="삼성전자"),
            ]
        brokerage = MockBrokerage(
            balance=make_balance(equity=equity, cash=equity - sum(p.current_price * p.quantity for p in positions)),
            positions=positions,
        )
        kill_switch = KillSwitch(lock_path=tmp_path / "kill.lock")
        monitor = LiveMonitor(brokerage, kill_switch)
        monitor.initialize()
        return monitor

    def test_price_update_recalculates_pnl(self, tmp_path):
        """가격 변동 → P&L 재계산"""
        monitor = self._setup_monitor(tmp_path)

        # 50000 → 55000 (10% 상승)
        price_data = make_realtime_price(symbol="005930", price=55000)
        monitor.on_price("005930", price_data)

        state = monitor.state
        snap = state.positions["005930"]
        assert snap.current_price == 55000
        # P&L = (55000-50000)*100 = 500,000
        assert snap.unrealized_pnl == pytest.approx(500_000)

    def test_price_update_unknown_symbol_ignored(self, tmp_path):
        """보유하지 않은 종목 가격 → 무시"""
        monitor = self._setup_monitor(tmp_path)

        price_data = make_realtime_price(symbol="999999", price=10000)
        monitor.on_price("999999", price_data)

        state = monitor.state
        assert "999999" not in state.positions


class TestLiveMonitorDrawdown:
    """LiveMonitor 드로다운 감지 — 경고 및 킬 스위치"""

    def _setup_monitor_with_alerts(self, tmp_path, equity=10_000_000, dd_warn=-0.05, dd_halt=-0.08):
        """알림 시스템이 있는 LiveMonitor 반환"""
        positions = [
            make_position("005930", qty=100, avg=50000, cur=50000, name="삼성전자"),
        ]
        brokerage = MockBrokerage(
            balance=make_balance(equity=equity, cash=equity - 5_000_000),
            positions=positions,
        )
        kill_switch = KillSwitch(lock_path=tmp_path / "kill.lock")
        alerts = MagicMock(spec=AlertSystem)
        monitor = LiveMonitor(
            brokerage, kill_switch,
            dd_warn_threshold=dd_warn,
            dd_halt_threshold=dd_halt,
            alert_system=alerts,
        )
        monitor.initialize()
        return monitor, kill_switch, alerts

    def test_dd_warn_fires_alert(self, tmp_path):
        """DD가 warn 임계값 미만 → dd_warning 알림"""
        monitor, kill_switch, alerts = self._setup_monitor_with_alerts(tmp_path)

        # 50000 → 47000 (6% 하락)
        # P&L = (47000-50000)*100 = -300,000
        # total_equity = 10,000,000 - 300,000 = 9,700,000
        # DD = (9,700,000 - 10,000,000) / 10,000,000 * 100 = -3.0% → 비율 -0.03
        # 더 큰 하락이 필요: 47000 → 45000
        # P&L = (45000-50000)*100 = -500,000
        # total_equity = 9,500,000 → DD = -5.0% → 비율 -0.05 (임계값과 같음)
        # on_price는 `<=` 비교이므로 정확히 -5%에서 발화
        # 좀 더 아래로: 44500 → P&L = -550,000 → equity = 9,450,000
        # DD = -5.5% → 비율 -0.055 ≤ -0.05 → 발화
        price_data = make_realtime_price(symbol="005930", price=44500)
        monitor.on_price("005930", price_data)

        alerts.dd_warning.assert_called_once()

    def test_dd_halt_activates_kill_switch(self, tmp_path):
        """DD가 halt 임계값 미만 → 킬 스위치 활성화"""
        monitor, kill_switch, alerts = self._setup_monitor_with_alerts(tmp_path)

        # P&L = (42000-50000)*100 = -800,000
        # total_equity = 9,200,000 → DD = -8.0% → 비율 -0.08
        # 더 아래로: 41500 → P&L = -850,000 → equity = 9,150,000
        # DD = -8.5% → 비율 -0.085 ≤ -0.08 → 킬 스위치
        price_data = make_realtime_price(symbol="005930", price=41500)
        monitor.on_price("005930", price_data)

        assert kill_switch.is_active is True
        alerts.kill_switch_activated.assert_called_once()

    def test_dd_warn_dedup(self, tmp_path):
        """경고는 한 번만 발화 (중복 방지)"""
        monitor, kill_switch, alerts = self._setup_monitor_with_alerts(tmp_path)

        # 1차: DD -5.5% → 경고
        monitor.on_price("005930", make_realtime_price(symbol="005930", price=44500))
        # 2차: DD -6.0% → 경고 재발화 안 됨
        monitor.on_price("005930", make_realtime_price(symbol="005930", price=44000))

        assert alerts.dd_warning.call_count == 1

    def test_dd_recovery_resets_warn_flag(self, tmp_path):
        """DD 회복 → 경고 플래그 리셋 → 재하락 시 재발화"""
        monitor, kill_switch, alerts = self._setup_monitor_with_alerts(tmp_path)

        # 1차: DD -5.5% → 경고
        monitor.on_price("005930", make_realtime_price(symbol="005930", price=44500))
        assert alerts.dd_warning.call_count == 1

        # 회복: DD 0% (가격 복원)
        monitor.on_price("005930", make_realtime_price(symbol="005930", price=50000))

        # 2차: DD -5.5% → 경고 재발화
        monitor.on_price("005930", make_realtime_price(symbol="005930", price=44500))
        assert alerts.dd_warning.call_count == 2


class TestLiveMonitorHealthy:
    """LiveMonitor.is_healthy — 정상 상태 판단"""

    def test_healthy_normal(self, tmp_path):
        """킬 스위치 비활성 + DD 정상 → True"""
        brokerage = MockBrokerage(
            balance=make_balance(equity=10_000_000),
            positions=[make_position("005930", qty=10, avg=50000, cur=55000)],
        )
        kill_switch = KillSwitch(lock_path=tmp_path / "kill.lock")
        monitor = LiveMonitor(brokerage, kill_switch)
        monitor.initialize()

        assert monitor.is_healthy is True

    def test_unhealthy_kill_switch(self, tmp_path):
        """킬 스위치 활성 → False"""
        brokerage = MockBrokerage(
            balance=make_balance(equity=10_000_000),
            positions=[make_position("005930", qty=10, avg=50000, cur=55000)],
        )
        kill_switch = KillSwitch(lock_path=tmp_path / "kill.lock")
        monitor = LiveMonitor(brokerage, kill_switch)
        monitor.initialize()

        kill_switch.activate("테스트 킬 스위치")

        assert monitor.is_healthy is False
