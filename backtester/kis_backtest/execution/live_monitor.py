"""실시간 포지션 모니터 + 드로다운 알림

KISWebSocket 콜백을 래핑하여 보유 포지션의 실시간 P&L을 추적하고,
드로다운 임계값 초과 시 경고 또는 킬 스위치를 작동시킨다.

WebSocket 자체를 실행하지 않는다 — 메인 루프에서 조회할 수 있는
콜백과 모니터링 상태만 제공.

Flow:
    1. initialize() — REST로 현재 포지션/잔고 조회, 초기 상태 설정
    2. setup_websocket(ws) — KISWebSocket에 콜백 등록
    3. 메인 루프에서 ws.start() → on_price/on_fill 콜백 호출
    4. state 속성으로 현재 상태 조회

드로다운 감시:
    - dd_warn_threshold 이하 → 경고 알림
    - dd_halt_threshold 이하 → 킬 스위치 활성화 + 알림
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from kis_backtest.execution.kill_switch import KillSwitch
from kis_backtest.models import AccountBalance, Position

if TYPE_CHECKING:
    from kis_backtest.execution.alerts import AlertSystem
    from kis_backtest.execution.order_executor import BrokerageProvider
    from kis_backtest.providers.kis.websocket import (
        FillNotice,
        KISWebSocket,
        RealtimePrice,
    )

logger = logging.getLogger(__name__)


# ============================================
# 데이터 모델
# ============================================


@dataclass
class PositionSnapshot:
    """단일 포지션의 실시간 스냅샷"""

    symbol: str
    name: str
    quantity: int
    avg_price: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    last_updated: datetime

    @staticmethod
    def from_position(pos: Position, now: Optional[datetime] = None) -> PositionSnapshot:
        """Position 모델에서 스냅샷 생성

        Args:
            pos: KIS 포지션 객체
            now: 생성 시각 (None이면 현재 시각)

        Returns:
            PositionSnapshot 인스턴스
        """
        return PositionSnapshot(
            symbol=pos.symbol,
            name=pos.name or pos.symbol,
            quantity=pos.quantity,
            avg_price=pos.average_price,
            current_price=pos.current_price,
            unrealized_pnl=pos.unrealized_pnl,
            unrealized_pnl_pct=pos.unrealized_pnl_percent,
            last_updated=now or datetime.now(),
        )


@dataclass
class MonitorState:
    """전체 모니터링 상태

    LiveMonitor._state로 관리되며, 가격 업데이트마다 갱신.
    스레드 안전은 LiveMonitor가 Lock으로 보장.
    """

    positions: Dict[str, PositionSnapshot] = field(default_factory=dict)
    total_equity: float = 0.0
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    peak_equity: float = 0.0
    current_dd: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)

    def summary(self) -> str:
        """현재 상태 한줄 요약

        Returns:
            사람이 읽을 수 있는 상태 문자열
        """
        pos_count = len(self.positions)
        lines = [
            f"=== 실시간 모니터 상태 ({self.last_updated:%H:%M:%S}) ===",
            f"총평가: {self.total_equity:,.0f}원 | "
            f"손익: {self.total_pnl:+,.0f}원 ({self.total_pnl_pct:+.2f}%)",
            f"고점: {self.peak_equity:,.0f}원 | DD: {self.current_dd:.2f}%",
            f"보유종목: {pos_count}개",
        ]
        if self.positions:
            lines.append("--- 포지션 ---")
            for snap in self.positions.values():
                lines.append(
                    f"  {snap.name}({snap.symbol}): "
                    f"{snap.quantity}주 × {snap.current_price:,.0f}원 "
                    f"| 손익 {snap.unrealized_pnl:+,.0f}원 "
                    f"({snap.unrealized_pnl_pct:+.2f}%)"
                )
        return "\n".join(lines)


# ============================================
# LiveMonitor
# ============================================


class LiveMonitor:
    """실시간 포지션 모니터

    KISWebSocket의 price/fill 콜백을 수신하여 포지션별 P&L을 추적하고,
    드로다운이 임계값을 넘으면 AlertSystem 경고 또는 KillSwitch를 작동시킨다.

    Usage:
        monitor = LiveMonitor(brokerage, kill_switch, alert_system=alerts)
        state = monitor.initialize()
        monitor.setup_websocket(ws)
        ws.start()  # 블로킹 — on_price/on_fill 콜백 호출

        # 다른 스레드에서 상태 조회
        print(monitor.state.summary())
    """

    def __init__(
        self,
        brokerage: BrokerageProvider,
        kill_switch: KillSwitch,
        dd_warn_threshold: float = -0.05,
        dd_halt_threshold: float = -0.08,
        alert_system: Optional[AlertSystem] = None,
    ) -> None:
        """LiveMonitor 초기화

        Args:
            brokerage: 잔고/포지션 조회용 브로커리지 인터페이스
            kill_switch: 파일 기반 긴급 정지 스위치
            dd_warn_threshold: DD 경고 임계값 (기본 -5%, 음수)
            dd_halt_threshold: DD 정지 임계값 (기본 -8%, 음수)
            alert_system: 알림 시스템 (None이면 로거만 사용)
        """
        self._brokerage = brokerage
        self._kill_switch = kill_switch
        self._dd_warn = dd_warn_threshold
        self._dd_halt = dd_halt_threshold
        self._alerts = alert_system

        self._state = MonitorState()
        self._initial_equity: float = 0.0
        self._lock = threading.Lock()

        # 드로다운 경고 중복 방지 플래그
        self._dd_warn_fired = False

    # ── 초기화 ──────────────────────────────────────────────

    def initialize(self) -> MonitorState:
        """REST API로 현재 상태 조회 후 모니터 초기화

        브로커리지에서 잔고와 포지션을 가져와 초기 MonitorState를 구성.
        peak_equity를 현재 총평가로 설정.

        Returns:
            초기화된 MonitorState
        """
        balance = self._brokerage.get_balance()
        positions = self._brokerage.get_positions()

        now = datetime.now()
        snapshots: Dict[str, PositionSnapshot] = {}
        for pos in positions:
            snapshots[pos.symbol] = PositionSnapshot.from_position(pos, now)

        total_equity = balance.total_equity
        self._initial_equity = total_equity

        with self._lock:
            self._state = MonitorState(
                positions=snapshots,
                total_equity=total_equity,
                total_pnl=balance.total_pnl,
                total_pnl_pct=balance.total_pnl_percent,
                peak_equity=total_equity,
                current_dd=0.0,
                last_updated=now,
            )
            state_copy = self._copy_state()

        logger.info(
            "모니터 초기화 완료: 총평가 %s원, 보유종목 %d개",
            f"{total_equity:,.0f}",
            len(snapshots),
        )

        if self._alerts is not None:
            self._alerts.info(
                "모니터 시작",
                f"총평가 {total_equity:,.0f}원, 보유 {len(snapshots)}종목",
            )

        return state_copy

    # ── WebSocket 콜백 ──────────────────────────────────────

    def on_price(self, symbol: str, data: RealtimePrice) -> None:
        """KISWebSocket 실시간 체결가 콜백

        포지션에 해당하는 종목의 현재가를 갱신하고,
        P&L과 드로다운을 재계산한다.

        Args:
            symbol: 종목코드
            data: 실시간 체결가 데이터
        """
        with self._lock:
            snap = self._state.positions.get(symbol)
            if snap is None:
                return

            now = datetime.now()
            new_price = float(data.price)

            # 포지션 스냅샷 갱신 (새 객체 생성 — 불변 패턴)
            pnl = (new_price - snap.avg_price) * snap.quantity
            pnl_pct = (
                ((new_price - snap.avg_price) / snap.avg_price * 100.0)
                if snap.avg_price > 0
                else 0.0
            )

            self._state.positions[symbol] = PositionSnapshot(
                symbol=snap.symbol,
                name=snap.name,
                quantity=snap.quantity,
                avg_price=snap.avg_price,
                current_price=new_price,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=pnl_pct,
                last_updated=now,
            )

            self._recalculate_unlocked(now)

        # Lock 밖에서 드로다운 체크 (알림은 Lock 불필요)
        self._check_drawdown()

    def on_fill(self, notice: FillNotice) -> None:
        """KISWebSocket 체결 통보 콜백

        체결 이벤트를 로깅한다. 포지션 변경은 다음 initialize() 또는
        별도 REST 재조회에서 반영.

        Args:
            notice: 체결 통보 데이터
        """
        side_kr = "매도" if notice.side == "01" else "매수"
        logger.info(
            "체결 통보: %s %s %d주 × %s원 (주문번호: %s)",
            side_kr,
            notice.symbol,
            notice.fill_qty,
            f"{notice.fill_price:,}",
            notice.order_no,
        )

        if self._alerts is not None and notice.is_fill:
            self._alerts.order_executed(
                trade_summary=f"{side_kr} {notice.symbol} {notice.fill_qty}주",
                amount=float(notice.fill_qty * notice.fill_price),
            )

    # ── WebSocket 설정 ──────────────────────────────────────

    def setup_websocket(self, ws: KISWebSocket) -> None:
        """KISWebSocket에 모니터 콜백 등록

        현재 보유 종목의 실시간 체결가와 체결 통보를 구독한다.

        Args:
            ws: KISWebSocket 인스턴스 (start() 호출 전)
        """
        symbols = list(self._state.positions.keys())

        if symbols:
            ws.subscribe_price(symbols, self.on_price)
            logger.info("실시간 체결가 구독: %s", symbols)
        else:
            logger.warning("보유 종목 없음 — 체결가 구독 스킵")

        ws.subscribe_fills(self.on_fill)
        logger.info("체결 통보 구독 완료")

    # ── 내부 계산 ──────────────────────────────────────────

    def _recalculate_unlocked(self, now: datetime) -> None:
        """총평가/P&L/드로다운 재계산 (Lock 보유 상태에서 호출)

        Args:
            now: 현재 시각
        """
        # 포지션 평가금액 합산
        total_market_value = sum(
            s.current_price * s.quantity for s in self._state.positions.values()
        )

        # 총평가 = 초기 현금 + 포지션 평가 변동
        # 단순화: 포지션 P&L 합산으로 총평가 추정
        total_pnl = sum(
            s.unrealized_pnl for s in self._state.positions.values()
        )

        # 초기 자산 대비 P&L%
        total_pnl_pct = (
            (total_pnl / self._initial_equity * 100.0)
            if self._initial_equity > 0
            else 0.0
        )

        total_equity = self._initial_equity + total_pnl

        # 고점 갱신
        peak = max(self._state.peak_equity, total_equity)

        # 드로다운 (음수)
        dd = (
            ((total_equity - peak) / peak * 100.0)
            if peak > 0
            else 0.0
        )

        self._state.total_equity = total_equity
        self._state.total_pnl = total_pnl
        self._state.total_pnl_pct = total_pnl_pct
        self._state.peak_equity = peak
        self._state.current_dd = dd
        self._state.last_updated = now

    def _check_drawdown(self) -> None:
        """드로다운 임계값 검사 → 경고 또는 킬 스위치 작동"""
        with self._lock:
            dd = self._state.current_dd

        dd_pct = dd / 100.0  # % → 비율 변환 (임계값은 비율)

        # 킬 스위치 임계값 (-8% 기본)
        if dd_pct <= self._dd_halt:
            reason = (
                f"드로다운 {dd:.2f}% — "
                f"정지 임계값 {self._dd_halt * 100:.1f}% 초과"
            )
            logger.critical("KILL SWITCH: %s", reason)
            self._kill_switch.activate(reason)

            if self._alerts is not None:
                self._alerts.kill_switch_activated(reason)
            return

        # 경고 임계값 (-5% 기본) — 한 번만 발화
        if dd_pct <= self._dd_warn and not self._dd_warn_fired:
            self._dd_warn_fired = True
            logger.warning(
                "DD 경고: %.2f%% (임계값 %.1f%%)",
                dd,
                self._dd_warn * 100,
            )

            if self._alerts is not None:
                self._alerts.dd_warning(
                    current_dd=dd,
                    threshold=self._dd_warn * 100,
                )
            return

        # 경고 해제 (DD가 경고 임계값 위로 복귀)
        if dd_pct > self._dd_warn and self._dd_warn_fired:
            self._dd_warn_fired = False
            logger.info("DD 경고 해제: %.2f%%", dd)

    def _copy_state(self) -> MonitorState:
        """현재 상태의 방어적 복사본 생성 (Lock 보유 상태에서 호출)

        Returns:
            MonitorState 복사본
        """
        return MonitorState(
            positions=dict(self._state.positions),
            total_equity=self._state.total_equity,
            total_pnl=self._state.total_pnl,
            total_pnl_pct=self._state.total_pnl_pct,
            peak_equity=self._state.peak_equity,
            current_dd=self._state.current_dd,
            last_updated=self._state.last_updated,
        )

    # ── 속성 ──────────────────────────────────────────────

    @property
    def state(self) -> MonitorState:
        """현재 모니터링 상태 (스레드 안전 복사본)

        Returns:
            MonitorState 스냅샷
        """
        with self._lock:
            return self._copy_state()

    @property
    def is_healthy(self) -> bool:
        """모니터 정상 상태 여부

        킬 스위치가 비활성이고 DD가 정지 임계값 이내이면 True.

        Returns:
            정상 여부
        """
        if self._kill_switch.is_active:
            return False

        with self._lock:
            dd_pct = self._state.current_dd / 100.0
        return dd_pct > self._dd_halt
