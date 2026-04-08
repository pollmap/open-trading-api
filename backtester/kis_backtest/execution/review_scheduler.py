"""복기 스케줄러

일간 스냅샷(16:00 KST)과 주간 복기(금요일 16:30 KST)를 오케스트레이션.
BrokerageProvider, ReviewEngine, VaultWriter를 조율하여
포트폴리오 상태를 기록하고 주간 성과를 분석한다.

Flow:
    매일 16:00 KST (장 마감 후)
      ↓
    run_daily() → DailySnapshot + Vault 기록
      ↓
    매주 금요일 16:30 KST
      ↓
    run_weekly() → WeeklyReport + 드리프트 체크 + Vault 기록
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Protocol, Tuple

from kis_backtest.portfolio.review_engine import (
    KillCondition,
    ReviewEngine,
    TradeRecord,
    WeeklyReport,
)

if TYPE_CHECKING:
    from kis_backtest.execution.capital_ladder import CapitalLadder
    from kis_backtest.models.trading import AccountBalance, Position

logger = logging.getLogger(__name__)

# KST 타임존 (UTC+9)
KST = timezone(timedelta(hours=9))

# 스케줄 상수
_DAILY_HOUR = 16
_DAILY_MINUTE = 0
_WEEKLY_HOUR = 16
_WEEKLY_MINUTE = 30
_FRIDAY = 4  # datetime.weekday(): 0=월 ~ 4=금
_WEEKEND = {5, 6}

# 드리프트 임계값 (5%p)
_DRIFT_THRESHOLD = 0.05


class BrokerageProvider(Protocol):
    """브로커리지 제공자 프로토콜 (복기 스케줄러용 최소 인터페이스)"""

    def get_balance(self) -> AccountBalance: ...

    def get_positions(self) -> list[Position]: ...


class VaultWriter(Protocol):
    """Vault 저장 프로토콜 (병렬 구현 중 — 인터페이스만 사용)"""

    def write_daily_snapshot(
        self,
        date: str,
        balance: AccountBalance,
        positions: list[Position],
        equity_curve_value: float,
        dd_from_peak: float,
        notes: str = "",
    ) -> Path: ...

    def write_weekly_report(self, report: WeeklyReport) -> Path: ...

    def write_trade_log(
        self, date: str, trades: list[TradeRecord]
    ) -> Path: ...

    def append_equity_curve(self, date: str, equity: float) -> None: ...


@dataclass(frozen=True)
class DailySnapshot:
    """일간 포트폴리오 스냅샷 (불변)"""

    date: str  # "2026-04-07"
    equity: float
    cash: float
    positions_count: int
    dd_from_peak: float
    file_path: Optional[str] = None


class ReviewScheduler:
    """복기 스케줄러

    매일 장 마감 후 포트폴리오 상태를 스냅샷하고,
    매주 금요일에 주간 복기를 실행한다.

    Usage:
        scheduler = ReviewScheduler(
            brokerage=kis_brokerage,
            review_engine=engine,
            vault_writer=vault,
            kill_conditions=[KillCondition(...)],
        )

        # 매일 16:00 KST
        if scheduler.should_run_daily():
            snapshot = scheduler.run_daily()

        # 매주 금요일 16:30 KST
        if scheduler.should_run_weekly():
            report = scheduler.run_weekly()
    """

    def __init__(
        self,
        brokerage: BrokerageProvider,
        review_engine: ReviewEngine,
        vault_writer: VaultWriter,
        kill_conditions: Optional[List[KillCondition]] = None,
        initial_capital: float = 5_000_000,
        capital_ladder: Optional["CapitalLadder"] = None,
    ) -> None:
        """ReviewScheduler 초기화

        Args:
            brokerage: 브로커리지 제공자 (잔고/포지션 조회)
            review_engine: 주간 복기 엔진
            vault_writer: Vault 저장 담당
            kill_conditions: 투자논지 반증 조건 목록
            initial_capital: 초기 투자금 (원)
            capital_ladder: Capital Ladder (점진적 자본 배포, 선택)
        """
        self._brokerage = brokerage
        self._engine = review_engine
        self._vault = vault_writer
        self._kill_conditions = kill_conditions or []
        self._initial_capital = initial_capital
        self._ladder = capital_ladder

        self._equity_history: List[Tuple[str, float]] = []
        self._trade_buffer: List[TradeRecord] = []
        self._daily_snapshots: List[DailySnapshot] = []
        self._peak_equity: float = initial_capital
        self._weekly_dates: set[str] = set()  # 주간 복기 실행 기록 (ISO week)

    # ── 일간 스냅샷 ─────────────────────────────────────────────

    def run_daily(self, date: Optional[str] = None) -> DailySnapshot:
        """일간 스냅샷 실행

        브로커리지에서 잔고와 포지션을 조회하고,
        Vault에 기록한 뒤 DailySnapshot을 반환한다.

        Args:
            date: 스냅샷 날짜 ("YYYY-MM-DD"). None이면 현재 KST 기준.

        Returns:
            DailySnapshot (frozen dataclass)
        """
        if date is None:
            date = datetime.now(KST).strftime("%Y-%m-%d")

        balance = self._brokerage.get_balance()
        positions = self._brokerage.get_positions()

        equity = balance.total_equity
        cash = balance.total_cash

        # 고점 갱신 + 드로다운 계산
        self._peak_equity = max(self._peak_equity, equity)
        dd_from_peak = (
            (equity - self._peak_equity) / self._peak_equity
            if self._peak_equity > 0
            else 0.0
        )

        # Vault 기록
        file_path = self._vault.write_daily_snapshot(
            date=date,
            balance=balance,
            positions=positions,
            equity_curve_value=equity,
            dd_from_peak=dd_from_peak,
        )
        self._vault.append_equity_curve(date, equity)

        # 내부 히스토리 누적
        self._equity_history.append((date, equity))

        snapshot = DailySnapshot(
            date=date,
            equity=equity,
            cash=cash,
            positions_count=len(positions),
            dd_from_peak=dd_from_peak,
            file_path=str(file_path) if file_path else None,
        )
        self._daily_snapshots.append(snapshot)

        # Capital Ladder 업데이트 (연결 시)
        ladder_event = None
        if self._ladder is not None:
            ladder_event = self._ladder.update(equity, dt=date)
            if ladder_event:
                logger.info("래더 이벤트: %s", ladder_event)

        logger.info(
            "일간 스냅샷 완료: %s | 자산 %s원 | DD %.2f%% | 종목 %d개%s",
            date,
            f"{equity:,.0f}",
            dd_from_peak * 100,
            len(positions),
            f" | 래더: {ladder_event}" if ladder_event else "",
        )

        return snapshot

    # ── 주간 복기 ───────────────────────────────────────────────

    def run_weekly(
        self,
        period_start: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> WeeklyReport:
        """주간 복기 실행

        이번 주 equity curve와 거래 버퍼를 ReviewEngine에 전달하여
        WeeklyReport를 생성하고, Vault에 기록한다.

        Args:
            period_start: 기간 시작일. None이면 이번 주 equity_history의 첫 날짜.
            period_end: 기간 종료일. None이면 현재 KST 날짜.

        Returns:
            WeeklyReport
        """
        now_str = datetime.now(KST).strftime("%Y-%m-%d")

        # 기간 결정
        if period_end is None:
            period_end = now_str

        # 이번 주 equity curve 추출
        week_entries = self._get_this_week_entries(period_end)

        if period_start is None:
            period_start = week_entries[0][0] if week_entries else period_end

        # equity curve 값 목록 (ReviewEngine 입력)
        equity_curve = [entry[1] for entry in week_entries]
        if not equity_curve:
            equity_curve = [self._peak_equity]

        # 복기 실행
        report = self._engine.weekly_review(
            equity_curve=equity_curve,
            trades=self._trade_buffer if self._trade_buffer else None,
            kill_conditions=self._kill_conditions if self._kill_conditions else None,
            period_start=period_start,
            period_end=period_end,
        )

        # Vault 기록
        self._vault.write_weekly_report(report)

        if self._trade_buffer:
            self._vault.write_trade_log(period_end, self._trade_buffer)

        # 드리프트 체크
        try:
            positions = self._brokerage.get_positions()
            balance = self._brokerage.get_balance()
            drift_warnings = self.check_drift(positions, balance.total_equity)
            for warning in drift_warnings:
                logger.warning("드리프트 경고: %s", warning)
        except Exception:
            logger.warning("드리프트 체크 실패 (브로커리지 조회 오류)", exc_info=True)

        # 거래 버퍼 초기화
        trades_count = len(self._trade_buffer)
        self._trade_buffer = []

        # 주간 실행 기록
        iso_week = _iso_week_key(
            datetime.strptime(period_end, "%Y-%m-%d").replace(tzinfo=KST)
        )
        self._weekly_dates.add(iso_week)

        logger.info(
            "주간 복기 완료: %s ~ %s | 수익률 %+.2f%% | 거래 %d건",
            period_start,
            period_end,
            report.portfolio_return * 100,
            trades_count,
        )

        return report

    # ── 거래 버퍼 ───────────────────────────────────────────────

    def add_trades(self, trades: List[TradeRecord]) -> None:
        """거래 기록을 버퍼에 추가

        다음 주간 복기 시 포함된다.

        Args:
            trades: 추가할 거래 기록 목록
        """
        self._trade_buffer.extend(trades)
        logger.debug("거래 %d건 버퍼에 추가 (총 %d건)", len(trades), len(self._trade_buffer))

    # ── 스케줄 판단 ─────────────────────────────────────────────

    def should_run_daily(self, now: Optional[datetime] = None) -> bool:
        """일간 스냅샷 실행 여부 판단

        평일이고 16:00 KST 이후이며, 오늘 스냅샷이 아직 없으면 True.

        Args:
            now: 기준 시각. None이면 현재 KST.

        Returns:
            실행 여부
        """
        now = _ensure_kst(now)

        # 주말 제외
        if now.weekday() in _WEEKEND:
            return False

        # 16:00 이전이면 아직 장 마감 전
        if (now.hour, now.minute) < (_DAILY_HOUR, _DAILY_MINUTE):
            return False

        # 오늘 이미 스냅샷이 있으면 스킵
        today_str = now.strftime("%Y-%m-%d")
        return not any(s.date == today_str for s in self._daily_snapshots)

    def should_run_weekly(self, now: Optional[datetime] = None) -> bool:
        """주간 복기 실행 여부 판단

        금요일이고 16:30 KST 이후이며, 이번 주 복기가 아직 없으면 True.

        Args:
            now: 기준 시각. None이면 현재 KST.

        Returns:
            실행 여부
        """
        now = _ensure_kst(now)

        # 금요일만
        if now.weekday() != _FRIDAY:
            return False

        # 16:30 이전이면 아직
        if (now.hour, now.minute) < (_WEEKLY_HOUR, _WEEKLY_MINUTE):
            return False

        # 이번 주 이미 복기했으면 스킵
        iso_week = _iso_week_key(now)
        return iso_week not in self._weekly_dates

    # ── 드리프트 체크 ───────────────────────────────────────────

    def check_drift(
        self, positions: List[Position], total_equity: float
    ) -> List[str]:
        """포지션 드리프트 체크

        현재 비중과 균등 분배 비중을 비교하여
        5%p 이상 벗어난 포지션에 대해 경고를 반환한다.

        Args:
            positions: 현재 포지션 목록
            total_equity: 총 자산 (현금 포함)

        Returns:
            드리프트 경고 메시지 목록
        """
        if not positions or total_equity <= 0:
            return []

        target_weight = 1.0 / len(positions)
        warnings: List[str] = []

        for pos in positions:
            market_value = pos.quantity * pos.current_price
            current_weight = market_value / total_equity
            deviation = abs(current_weight - target_weight)

            if deviation > _DRIFT_THRESHOLD:
                name = pos.name or pos.symbol
                warnings.append(
                    f"{name}({pos.symbol}): "
                    f"현재 {current_weight:.1%} vs 목표 {target_weight:.1%} "
                    f"(편차 {deviation:.1%})"
                )

        return warnings

    # ── 프로퍼티 ────────────────────────────────────────────────

    @property
    def equity_history(self) -> List[Tuple[str, float]]:
        """전체 equity history (date, equity) 목록"""
        return list(self._equity_history)

    @property
    def latest_snapshot(self) -> Optional[DailySnapshot]:
        """가장 최근 일간 스냅샷"""
        if not self._daily_snapshots:
            return None
        return self._daily_snapshots[-1]

    # ── 내부 헬퍼 ───────────────────────────────────────────────

    def _get_this_week_entries(
        self, reference_date: str
    ) -> List[Tuple[str, float]]:
        """이번 주 equity history 항목 추출

        reference_date가 속한 주(월~일)의 항목만 필터링.

        Args:
            reference_date: 기준 날짜 ("YYYY-MM-DD")

        Returns:
            이번 주에 해당하는 (date, equity) 항목 목록
        """
        ref = datetime.strptime(reference_date, "%Y-%m-%d").replace(tzinfo=KST)
        # 이번 주 월요일
        monday = ref - timedelta(days=ref.weekday())
        monday_str = monday.strftime("%Y-%m-%d")
        # 이번 주 일요일
        sunday = monday + timedelta(days=6)
        sunday_str = sunday.strftime("%Y-%m-%d")

        return [
            (d, eq)
            for d, eq in self._equity_history
            if monday_str <= d <= sunday_str
        ]


# ── 모듈 레벨 유틸리티 ──────────────────────────────────────────


def _ensure_kst(now: Optional[datetime]) -> datetime:
    """datetime을 KST로 정규화

    Args:
        now: 입력 datetime. None이면 현재 KST.

    Returns:
        KST timezone이 설정된 datetime
    """
    if now is None:
        return datetime.now(KST)
    if now.tzinfo is None:
        return now.replace(tzinfo=KST)
    return now


def _iso_week_key(dt: datetime) -> str:
    """ISO 주차 키 생성

    Args:
        dt: 대상 datetime

    Returns:
        "2026-W15" 형태의 주차 문자열
    """
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"
