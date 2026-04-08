"""Phase 4 복기 시스템 테스트 — VaultWriter + ReviewScheduler

VaultWriter: Obsidian Vault에 일일 스냅샷, 주간 리포트, 거래 로그, 자산 곡선 CSV 저장
ReviewScheduler: 일간/주간 스케줄 오케스트레이션, 드리프트 체크, equity history 관리
"""

from __future__ import annotations

# ── sys.modules 패치 ────────────────────────────────────────────
# kis_backtest.execution.__init__ 이 fill_tracker를 import하면
# kis.auth → kis_auth → Crypto 체인이 실행되어 ModuleNotFoundError 발생.
# vault_writer, review_scheduler 모듈 자체는 Crypto에 의존하지 않으므로,
# fill_tracker의 의존 체인만 mock 처리하여 __init__ 로딩을 통과시킨다.

import sys
from types import ModuleType
from unittest.mock import MagicMock

_MOCK_MODULES = [
    "Crypto",
    "Crypto.Cipher",
    "Crypto.Cipher.AES",
    "Crypto.Util",
    "Crypto.Util.Padding",
    "kis_auth",
]
for _mod_name in _MOCK_MODULES:
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

import pytest

from kis_backtest.execution.review_scheduler import (
    DailySnapshot,
    ReviewScheduler,
)
from kis_backtest.execution.vault_writer import VaultWriter
from kis_backtest.models.trading import AccountBalance, Position
from kis_backtest.portfolio.review_engine import (
    KillCondition,
    ReviewEngine,
    TradeRecord,
    WeeklyReport,
)

# ── 상수 ────────────────────────────────────────────────────────

KST = timezone(timedelta(hours=9))


# ── Mock 헬퍼 ───────────────────────────────────────────────────


def _make_balance(
    total_cash: float = 5_000_000,
    available_cash: float = 1_000_000,
    total_equity: float = 5_000_000,
    total_pnl: float = 0.0,
    total_pnl_percent: float = 0.0,
) -> AccountBalance:
    """테스트용 AccountBalance 생성"""
    return AccountBalance(
        total_cash=total_cash,
        available_cash=available_cash,
        total_equity=total_equity,
        total_pnl=total_pnl,
        total_pnl_percent=total_pnl_percent,
    )


def _make_position(
    symbol: str = "005930",
    quantity: int = 10,
    average_price: float = 50000,
    current_price: float = 55000,
    unrealized_pnl: float = 50000,
    unrealized_pnl_percent: float = 10.0,
    name: str = "삼성전자",
) -> Position:
    """테스트용 Position 생성"""
    return Position(
        symbol=symbol,
        quantity=quantity,
        average_price=average_price,
        current_price=current_price,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_percent=unrealized_pnl_percent,
        name=name,
    )


def _make_trade(
    date: str = "2026-04-07",
    ticker: str = "005930",
    action: str = "BUY",
    quantity: int = 10,
    price: float = 50000,
    amount: float = 500000,
    commission: float = 100,
    tax: float = 0,
    slippage: float = 50,
) -> TradeRecord:
    """테스트용 TradeRecord 생성"""
    return TradeRecord(
        date=date,
        ticker=ticker,
        action=action,
        quantity=quantity,
        price=price,
        amount=amount,
        commission=commission,
        tax=tax,
        slippage=slippage,
    )


class MockBrokerage:
    """브로커리지 프로토콜 Mock"""

    def __init__(
        self,
        balance: AccountBalance,
        positions: Optional[List[Position]] = None,
    ) -> None:
        self._balance = balance
        self._positions = positions or []

    def get_balance(self) -> AccountBalance:
        return self._balance

    def get_positions(self) -> list[Position]:
        return list(self._positions)


class MockVaultWriter:
    """VaultWriter 프로토콜 Mock — 모든 호출을 캡처"""

    def __init__(self) -> None:
        self.daily_calls: list[dict] = []
        self.weekly_calls: list[WeeklyReport] = []
        self.trade_calls: list[dict] = []
        self.equity_calls: list[dict] = []

    def write_daily_snapshot(
        self,
        date: str,
        balance: AccountBalance,
        positions: list[Position],
        equity_curve_value: float,
        dd_from_peak: float,
        notes: str = "",
    ) -> Path:
        """일일 스냅샷 호출 캡처"""
        self.daily_calls.append({
            "date": date,
            "balance": balance,
            "positions": positions,
            "equity_curve_value": equity_curve_value,
            "dd_from_peak": dd_from_peak,
            "notes": notes,
        })
        return Path("/tmp/test-daily.md")

    def write_weekly_report(self, report: WeeklyReport) -> Path:
        """주간 리포트 호출 캡처"""
        self.weekly_calls.append(report)
        return Path("/tmp/test-weekly.md")

    def write_trade_log(self, date: str, trades: list[TradeRecord]) -> Path:
        """거래 로그 호출 캡처"""
        self.trade_calls.append({"date": date, "trades": trades})
        return Path("/tmp/test-trades.md")

    def append_equity_curve(self, date: str, equity: float) -> None:
        """자산 곡선 호출 캡처"""
        self.equity_calls.append({"date": date, "equity": equity})


# ═══════════════════════════════════════════════════════════════
#  TestVaultWriter — 파일 I/O 기반 테스트 (tmp_path 사용)
# ═══════════════════════════════════════════════════════════════


class TestVaultWriter:
    """VaultWriter 파일 저장 테스트"""

    def _make_writer(self, tmp_path: Path) -> VaultWriter:
        return VaultWriter(vault_root=tmp_path)

    # ── 일일 스냅샷 ─────────────────────────────────────────

    def test_write_daily_snapshot_creates_file(self, tmp_path: Path) -> None:
        """일일 스냅샷 파일이 daily_dir/date.md 경로에 생성되는지 확인"""
        writer = self._make_writer(tmp_path)
        balance = _make_balance()
        positions = [_make_position()]

        result = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=balance,
            positions=positions,
            equity_curve_value=5_000_000,
            dd_from_peak=-2.3,
        )

        assert result.exists()
        assert result == writer.daily_dir / "2026-04-07.md"

    def test_daily_snapshot_frontmatter(self, tmp_path: Path) -> None:
        """YAML frontmatter에 date, type, equity, dd, tags 포함 확인"""
        writer = self._make_writer(tmp_path)
        balance = _make_balance(total_equity=5_150_000)
        path = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=balance,
            positions=[],
            equity_curve_value=5_150_000,
            dd_from_peak=-1.5,
        )

        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "date: 2026-04-07" in content
        assert "type: daily-snapshot" in content
        assert "equity: 5150000" in content
        assert "dd: -1.5" in content
        assert "tags: [trading, daily]" in content

    def test_daily_snapshot_balance_table(self, tmp_path: Path) -> None:
        """계좌 현황 테이블에 잔고 값 포함 확인"""
        writer = self._make_writer(tmp_path)
        balance = _make_balance(
            total_equity=5_200_000,
            available_cash=1_200_000,
            total_pnl=200_000,
            total_pnl_percent=4.0,
        )
        path = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=balance,
            positions=[],
            equity_curve_value=5_200_000,
            dd_from_peak=0.0,
        )

        content = path.read_text(encoding="utf-8")
        assert "5,200,000" in content
        assert "1,200,000" in content
        assert "200,000" in content

    def test_daily_snapshot_positions_table(self, tmp_path: Path) -> None:
        """보유종목 테이블에 포지션 행 포함 확인"""
        writer = self._make_writer(tmp_path)
        positions = [
            _make_position(symbol="005930", name="삼성전자", quantity=10),
            _make_position(
                symbol="000660",
                name="SK하이닉스",
                quantity=5,
                average_price=120000,
                current_price=130000,
                unrealized_pnl=50000,
                unrealized_pnl_percent=8.3,
            ),
        ]
        path = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=_make_balance(),
            positions=positions,
            equity_curve_value=5_000_000,
            dd_from_peak=0.0,
        )

        content = path.read_text(encoding="utf-8")
        assert "삼성전자" in content
        assert "SK하이닉스" in content
        assert "보유종목" in content

    def test_daily_snapshot_with_notes(self, tmp_path: Path) -> None:
        """notes 파라미터 전달 시 메모 섹션 포함 확인"""
        writer = self._make_writer(tmp_path)
        path = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=_make_balance(),
            positions=[],
            equity_curve_value=5_000_000,
            dd_from_peak=0.0,
            notes="리밸런싱 예정",
        )

        content = path.read_text(encoding="utf-8")
        assert "## 메모" in content
        assert "리밸런싱 예정" in content

    def test_daily_snapshot_no_positions(self, tmp_path: Path) -> None:
        """포지션 없을 때 보유종목 테이블 미출력 확인"""
        writer = self._make_writer(tmp_path)
        path = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=_make_balance(),
            positions=[],
            equity_curve_value=5_000_000,
            dd_from_peak=0.0,
        )

        content = path.read_text(encoding="utf-8")
        assert "보유종목" not in content

    # ── 주간 리포트 ─────────────────────────────────────────

    def test_write_weekly_report_creates_file(self, tmp_path: Path) -> None:
        """주간 리포트 파일이 weekly_dir/YYYY-Wnn.md 경로에 생성되는지 확인"""
        writer = self._make_writer(tmp_path)
        engine = ReviewEngine(initial_capital=5_000_000)
        report = engine.weekly_review(
            equity_curve=[5_000_000, 5_100_000],
            period_start="2026-03-30",
            period_end="2026-04-05",
        )

        result = writer.write_weekly_report(report)

        assert result.exists()
        assert result.parent == writer.weekly_dir
        assert result.name.endswith(".md")
        # 2026-04-05 -> ISO week 14
        assert "W14" in result.name

    def test_weekly_report_frontmatter(self, tmp_path: Path) -> None:
        """주간 리포트 frontmatter에 period, type, return, sharpe, tags 포함 확인"""
        writer = self._make_writer(tmp_path)
        engine = ReviewEngine(initial_capital=5_000_000)
        report = engine.weekly_review(
            equity_curve=[5_000_000, 5_100_000],
            period_start="2026-03-30",
            period_end="2026-04-05",
        )

        path = writer.write_weekly_report(report)
        content = path.read_text(encoding="utf-8")

        assert "period:" in content
        assert "type: weekly-review" in content
        assert "return:" in content
        assert "sharpe:" in content
        assert "tags: [trading, weekly, review]" in content

    # ── 거래 로그 ───────────────────────────────────────────

    def test_write_trade_log_creates_file(self, tmp_path: Path) -> None:
        """거래 로그 파일이 trades_dir/date-trades.md 경로에 생성되는지 확인"""
        writer = self._make_writer(tmp_path)
        trades = [_make_trade()]

        result = writer.write_trade_log("2026-04-07", trades)

        assert result.exists()
        assert result == writer.trades_dir / "2026-04-07-trades.md"

    def test_trade_log_table_rows(self, tmp_path: Path) -> None:
        """각 거래가 테이블 행으로 포함되는지 확인"""
        writer = self._make_writer(tmp_path)
        trades = [
            _make_trade(ticker="005930", action="BUY", quantity=10),
            _make_trade(ticker="000660", action="SELL", quantity=5, price=130000, amount=650000),
        ]

        path = writer.write_trade_log("2026-04-07", trades)
        content = path.read_text(encoding="utf-8")

        assert "005930" in content
        assert "000660" in content
        assert "BUY" in content
        assert "SELL" in content
        # 2건의 거래
        assert "2건" in content

    # ── 자산 곡선 CSV ───────────────────────────────────────

    def test_append_equity_curve_creates_csv(self, tmp_path: Path) -> None:
        """첫 호출 시 헤더 포함 CSV 생성 확인"""
        writer = self._make_writer(tmp_path)

        writer.append_equity_curve("2026-04-07", 5_000_000)

        csv_path = tmp_path / "02-Areas" / "trading-ops" / "equity_curve.csv"
        assert csv_path.exists()

        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = list(csv.reader(f))

        assert reader[0] == ["date", "equity"]
        assert reader[1][0] == "2026-04-07"
        assert reader[1][1] == "5000000"

    def test_append_equity_curve_appends(self, tmp_path: Path) -> None:
        """두 번째 호출 시 헤더 없이 행만 추가되는지 확인"""
        writer = self._make_writer(tmp_path)

        writer.append_equity_curve("2026-04-07", 5_000_000)
        writer.append_equity_curve("2026-04-08", 5_100_000)

        csv_path = tmp_path / "02-Areas" / "trading-ops" / "equity_curve.csv"

        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = list(csv.reader(f))

        # 헤더 1줄 + 데이터 2줄
        assert len(reader) == 3
        assert reader[0] == ["date", "equity"]
        assert reader[1][0] == "2026-04-07"
        assert reader[2][0] == "2026-04-08"

    # ── 디렉토리 프로퍼티 ───────────────────────────────────

    def test_directory_properties(self, tmp_path: Path) -> None:
        """daily_dir, weekly_dir, trades_dir 경로가 올바른지 확인"""
        writer = VaultWriter(vault_root=tmp_path, trading_area="02-Areas/trading-ops")
        base = tmp_path / "02-Areas" / "trading-ops"

        assert writer.daily_dir == base / "daily"
        assert writer.weekly_dir == base / "weekly"
        assert writer.trades_dir == base / "trades"

    def test_custom_vault_root(self, tmp_path: Path) -> None:
        """커스텀 vault_root 적용 확인"""
        custom = tmp_path / "my-vault"
        writer = VaultWriter(vault_root=custom, trading_area="ops")

        assert writer.daily_dir == custom / "ops" / "daily"
        assert writer.weekly_dir == custom / "ops" / "weekly"
        assert writer.trades_dir == custom / "ops" / "trades"

        # 실제 파일 저장 확인
        path = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=_make_balance(),
            positions=[],
            equity_curve_value=5_000_000,
            dd_from_peak=0.0,
        )
        assert path.exists()
        assert "my-vault" in str(path)


# ═══════════════════════════════════════════════════════════════
#  TestReviewScheduler — 비즈니스 로직 테스트 (Mock 사용)
# ═══════════════════════════════════════════════════════════════


class TestReviewScheduler:
    """ReviewScheduler 핵심 로직 테스트"""

    def _make_scheduler(
        self,
        balance: Optional[AccountBalance] = None,
        positions: Optional[List[Position]] = None,
        initial_capital: float = 5_000_000,
    ) -> tuple[ReviewScheduler, MockBrokerage, MockVaultWriter]:
        """테스트용 스케줄러 + 의존성 생성"""
        bal = balance or _make_balance()
        brokerage = MockBrokerage(bal, positions)
        vault = MockVaultWriter()
        engine = ReviewEngine(initial_capital=initial_capital)

        scheduler = ReviewScheduler(
            brokerage=brokerage,
            review_engine=engine,
            vault_writer=vault,
            initial_capital=initial_capital,
        )
        return scheduler, brokerage, vault

    # ── run_daily ───────────────────────────────────────────

    def test_run_daily_returns_snapshot(self) -> None:
        """run_daily가 올바른 필드를 가진 DailySnapshot 반환 확인"""
        scheduler, _, _ = self._make_scheduler(
            balance=_make_balance(total_equity=5_100_000, total_cash=1_000_000),
            positions=[_make_position()],
        )

        snapshot = scheduler.run_daily(date="2026-04-07")

        assert isinstance(snapshot, DailySnapshot)
        assert snapshot.date == "2026-04-07"
        assert snapshot.equity == 5_100_000
        assert snapshot.cash == 1_000_000
        assert snapshot.positions_count == 1
        assert snapshot.file_path is not None

    def test_run_daily_updates_equity_history(self) -> None:
        """run_daily 호출마다 equity_history에 항목 추가 확인"""
        scheduler, _, _ = self._make_scheduler()

        assert len(scheduler.equity_history) == 0

        scheduler.run_daily(date="2026-04-07")
        assert len(scheduler.equity_history) == 1

        scheduler.run_daily(date="2026-04-08")
        assert len(scheduler.equity_history) == 2

        assert scheduler.equity_history[0][0] == "2026-04-07"
        assert scheduler.equity_history[1][0] == "2026-04-08"

    def test_run_daily_updates_peak_equity(self) -> None:
        """peak_equity가 최대값을 추적하는지 확인"""
        scheduler, brokerage, _ = self._make_scheduler(
            balance=_make_balance(total_equity=5_500_000),
        )

        scheduler.run_daily(date="2026-04-07")

        # 자산 하락 시에도 peak 유지
        brokerage._balance = _make_balance(total_equity=5_200_000)
        scheduler.run_daily(date="2026-04-08")

        # peak는 5_500_000 유지
        assert scheduler._peak_equity == 5_500_000

    def test_run_daily_calculates_dd(self) -> None:
        """고점 대비 하락 시 dd_from_peak가 음수인지 확인"""
        scheduler, brokerage, _ = self._make_scheduler(
            balance=_make_balance(total_equity=5_500_000),
        )

        scheduler.run_daily(date="2026-04-07")

        # 자산 하락
        brokerage._balance = _make_balance(total_equity=5_000_000)
        snapshot = scheduler.run_daily(date="2026-04-08")

        # (5_000_000 - 5_500_000) / 5_500_000 = -0.0909...
        assert snapshot.dd_from_peak < 0
        assert snapshot.dd_from_peak == pytest.approx(-500_000 / 5_500_000, rel=0.01)

    def test_run_daily_calls_vault(self) -> None:
        """run_daily가 vault_writer.write_daily_snapshot을 호출하는지 확인"""
        scheduler, _, vault = self._make_scheduler(
            positions=[_make_position()],
        )

        scheduler.run_daily(date="2026-04-07")

        assert len(vault.daily_calls) == 1
        call = vault.daily_calls[0]
        assert call["date"] == "2026-04-07"
        assert len(vault.equity_calls) == 1

    # ── run_weekly ──────────────────────────────────────────

    def test_run_weekly_returns_report(self) -> None:
        """run_weekly가 올바른 기간의 WeeklyReport 반환 확인"""
        scheduler, _, _ = self._make_scheduler()

        # 먼저 daily로 equity history 쌓기
        scheduler.run_daily(date="2026-03-31")
        scheduler.run_daily(date="2026-04-01")
        scheduler.run_daily(date="2026-04-02")

        report = scheduler.run_weekly(
            period_start="2026-03-31",
            period_end="2026-04-02",
        )

        assert isinstance(report, WeeklyReport)
        assert report.period_start == "2026-03-31"
        assert report.period_end == "2026-04-02"

    def test_run_weekly_clears_trade_buffer(self) -> None:
        """run_weekly 후 trade_buffer가 비워지는지 확인"""
        scheduler, _, _ = self._make_scheduler()

        scheduler.add_trades([_make_trade(), _make_trade()])
        assert len(scheduler._trade_buffer) == 2

        scheduler.run_weekly(
            period_start="2026-04-01",
            period_end="2026-04-05",
        )

        assert len(scheduler._trade_buffer) == 0

    def test_run_weekly_uses_equity_history(self) -> None:
        """run_weekly가 누적된 equity curve를 엔진에 전달하는지 확인"""
        scheduler, _, vault = self._make_scheduler()

        scheduler.run_daily(date="2026-03-31")
        scheduler.run_daily(date="2026-04-01")

        scheduler.run_weekly(
            period_start="2026-03-31",
            period_end="2026-04-01",
        )

        # vault에 weekly report 저장 확인
        assert len(vault.weekly_calls) == 1

    # ── add_trades ──────────────────────────────────────────

    def test_add_trades_buffers(self) -> None:
        """add_trades로 추가한 거래가 주간 복기까지 버퍼에 유지 확인"""
        scheduler, _, _ = self._make_scheduler()

        trades_day1 = [_make_trade(date="2026-04-01")]
        trades_day2 = [
            _make_trade(date="2026-04-02", ticker="000660"),
            _make_trade(date="2026-04-02", ticker="035720"),
        ]

        scheduler.add_trades(trades_day1)
        scheduler.add_trades(trades_day2)

        assert len(scheduler._trade_buffer) == 3

    # ── latest_snapshot ─────────────────────────────────────

    def test_latest_snapshot_returns_most_recent(self) -> None:
        """latest_snapshot이 마지막 스냅샷을 반환하는지 확인"""
        scheduler, _, _ = self._make_scheduler()

        scheduler.run_daily(date="2026-04-07")
        scheduler.run_daily(date="2026-04-08")

        latest = scheduler.latest_snapshot
        assert latest is not None
        assert latest.date == "2026-04-08"

    def test_latest_snapshot_none_if_empty(self) -> None:
        """스냅샷이 없을 때 None 반환 확인"""
        scheduler, _, _ = self._make_scheduler()

        assert scheduler.latest_snapshot is None


# ═══════════════════════════════════════════════════════════════
#  TestScheduleLogic — 시간 기반 스케줄 판단 테스트
# ═══════════════════════════════════════════════════════════════


class TestScheduleLogic:
    """should_run_daily / should_run_weekly 스케줄 로직 테스트"""

    def _make_scheduler(self) -> ReviewScheduler:
        """스케줄 판단 전용 스케줄러 (최소 의존성)"""
        brokerage = MockBrokerage(_make_balance())
        vault = MockVaultWriter()
        engine = ReviewEngine(initial_capital=5_000_000)
        return ReviewScheduler(
            brokerage=brokerage,
            review_engine=engine,
            vault_writer=vault,
        )

    # ── should_run_daily ────────────────────────────────────

    def test_should_run_daily_weekday_after_1600(self) -> None:
        """평일(월) 16:05 KST -> True"""
        scheduler = self._make_scheduler()
        # 2026-04-06 월요일 16:05 KST
        now = datetime(2026, 4, 6, 16, 5, tzinfo=KST)

        assert scheduler.should_run_daily(now) is True

    def test_should_run_daily_weekday_before_1600(self) -> None:
        """평일(월) 15:59 KST -> False"""
        scheduler = self._make_scheduler()
        now = datetime(2026, 4, 6, 15, 59, tzinfo=KST)

        assert scheduler.should_run_daily(now) is False

    def test_should_run_daily_weekend(self) -> None:
        """토요일 -> False"""
        scheduler = self._make_scheduler()
        # 2026-04-11 토요일
        now = datetime(2026, 4, 11, 17, 0, tzinfo=KST)

        assert scheduler.should_run_daily(now) is False

    def test_should_run_daily_already_done(self) -> None:
        """오늘 이미 스냅샷이 있으면 False"""
        scheduler = self._make_scheduler()

        # 먼저 일간 스냅샷 실행
        scheduler.run_daily(date="2026-04-06")

        # 같은 날 다시 체크
        now = datetime(2026, 4, 6, 17, 0, tzinfo=KST)
        assert scheduler.should_run_daily(now) is False

    # ── should_run_weekly ───────────────────────────────────

    def test_should_run_weekly_friday_after_1630(self) -> None:
        """금요일 16:35 KST -> True"""
        scheduler = self._make_scheduler()
        # 2026-04-10 금요일 16:35 KST
        now = datetime(2026, 4, 10, 16, 35, tzinfo=KST)

        assert scheduler.should_run_weekly(now) is True

    def test_should_run_weekly_friday_before_1630(self) -> None:
        """금요일 16:25 KST -> False"""
        scheduler = self._make_scheduler()
        now = datetime(2026, 4, 10, 16, 25, tzinfo=KST)

        assert scheduler.should_run_weekly(now) is False

    def test_should_run_weekly_not_friday(self) -> None:
        """목요일 -> False"""
        scheduler = self._make_scheduler()
        # 2026-04-09 목요일
        now = datetime(2026, 4, 9, 17, 0, tzinfo=KST)

        assert scheduler.should_run_weekly(now) is False

    def test_should_run_weekly_already_done(self) -> None:
        """이번 주 이미 복기 완료 시 False"""
        scheduler = self._make_scheduler()

        # 주간 복기 실행 (2026-04-10 = 금요일, W15)
        scheduler.run_weekly(
            period_start="2026-04-06",
            period_end="2026-04-10",
        )

        # 같은 주 금요일 다시 체크
        now = datetime(2026, 4, 10, 18, 0, tzinfo=KST)
        assert scheduler.should_run_weekly(now) is False


# ═══════════════════════════════════════════════════════════════
#  TestDriftCheck — 포지션 드리프트 체크 테스트
# ═══════════════════════════════════════════════════════════════


class TestDriftCheck:
    """check_drift 포지션 비중 편차 테스트"""

    def _make_scheduler(self) -> ReviewScheduler:
        brokerage = MockBrokerage(_make_balance())
        vault = MockVaultWriter()
        engine = ReviewEngine(initial_capital=5_000_000)
        return ReviewScheduler(
            brokerage=brokerage,
            review_engine=engine,
            vault_writer=vault,
        )

    def test_no_drift_even_weights(self) -> None:
        """모든 포지션 비중이 균등할 때 경고 없음 확인"""
        scheduler = self._make_scheduler()

        # 4종목, 각 250만원 → 총 1000만원 포트폴리오에서 25%씩
        positions = [
            _make_position(symbol="A", quantity=100, current_price=25000, name="종목A"),
            _make_position(symbol="B", quantity=100, current_price=25000, name="종목B"),
            _make_position(symbol="C", quantity=100, current_price=25000, name="종목C"),
            _make_position(symbol="D", quantity=100, current_price=25000, name="종목D"),
        ]
        total_equity = 10_000_000  # 현금 포함 총 자산

        warnings = scheduler.check_drift(positions, total_equity)

        assert warnings == []

    def test_drift_detected(self) -> None:
        """한 포지션이 5%p 이상 편차 시 경고 발생 확인"""
        scheduler = self._make_scheduler()

        # 2종목, 목표 각 50%
        # A: 70만 / 100만 = 70% (편차 20%p)
        # B: 30만 / 100만 = 30% (편차 20%p)
        positions = [
            _make_position(symbol="A", quantity=70, current_price=10000, name="과대종목"),
            _make_position(symbol="B", quantity=30, current_price=10000, name="과소종목"),
        ]
        total_equity = 1_000_000

        warnings = scheduler.check_drift(positions, total_equity)

        assert len(warnings) == 2
        assert any("과대종목" in w for w in warnings)
        assert any("과소종목" in w for w in warnings)

    def test_drift_empty_positions(self) -> None:
        """빈 포지션 목록 시 경고 없음 확인"""
        scheduler = self._make_scheduler()

        warnings = scheduler.check_drift([], 5_000_000)

        assert warnings == []

    def test_drift_zero_equity(self) -> None:
        """총 자산이 0일 때 경고 없음 확인"""
        scheduler = self._make_scheduler()
        positions = [_make_position()]

        warnings = scheduler.check_drift(positions, 0.0)

        assert warnings == []
