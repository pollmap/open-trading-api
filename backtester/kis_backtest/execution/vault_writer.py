"""Vault 스냅샷 기록기

Obsidian 호환 마크다운으로 일일 스냅샷, 주간 복기 리포트, 거래 로그를 저장한다.
PARA 구조의 02-Areas/trading-ops/ 하위에 daily, weekly, trades 디렉토리를 사용.

Flow:
    AccountBalance + Position[]
      |
    VaultWriter.write_daily_snapshot()
      |
    {vault}/02-Areas/trading-ops/daily/2026-04-07.md

    WeeklyReport
      |
    VaultWriter.write_weekly_report()
      |
    {vault}/02-Areas/trading-ops/weekly/2026-W14.md

    TradeRecord[]
      |
    VaultWriter.write_trade_log()
      |
    {vault}/02-Areas/trading-ops/trades/2026-04-07-trades.md
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from kis_backtest.models.trading import AccountBalance, Position
from kis_backtest.portfolio.review_engine import TradeRecord, WeeklyReport

logger = logging.getLogger(__name__)


def _format_krw(value: float) -> str:
    """원화 포맷 (천 단위 콤마)"""
    return f"{value:,.0f}원"


def _format_pct(value: float, multiply: bool = True) -> str:
    """퍼센트 포맷. multiply=True이면 0.03 -> +3.00%"""
    v = value * 100 if multiply else value
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.2f}%"


def _format_signed_krw(value: float) -> str:
    """부호 포함 원화 포맷"""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.0f}원"


class VaultWriter:
    """Obsidian Vault에 트레이딩 기록을 저장하는 Writer

    일일 스냅샷, 주간 복기 리포트, 거래 로그, 자산 곡선 CSV를 관리한다.
    모든 파일은 UTF-8 인코딩으로 저장되며, YAML frontmatter를 포함하여
    Obsidian Dataview 플러그인과 호환된다.

    Usage:
        writer = VaultWriter()
        path = writer.write_daily_snapshot(
            date="2026-04-07",
            balance=balance,
            positions=positions,
            equity_curve_value=5_150_000,
            dd_from_peak=-2.3,
        )
    """

    def __init__(
        self,
        vault_root: Optional[Path] = None,
        trading_area: str = "02-Areas/trading-ops",
    ) -> None:
        """VaultWriter 초기화

        Args:
            vault_root: Obsidian Vault 루트 경로. 기본값 ~/obsidian-vault
            trading_area: Vault 내 트레이딩 영역 상대경로
        """
        self._vault = vault_root or Path.home() / "obsidian-vault"
        self._area = trading_area

    # ── 디렉토리 프로퍼티 ─────────────────────────────────────────

    @property
    def daily_dir(self) -> Path:
        """일일 스냅샷 디렉토리"""
        return self._vault / self._area / "daily"

    @property
    def weekly_dir(self) -> Path:
        """주간 리포트 디렉토리"""
        return self._vault / self._area / "weekly"

    @property
    def trades_dir(self) -> Path:
        """거래 로그 디렉토리"""
        return self._vault / self._area / "trades"

    # ── Public Methods ───────────────────────────────────────────

    def write_daily_snapshot(
        self,
        date: str,
        balance: AccountBalance,
        positions: List[Position],
        equity_curve_value: float,
        dd_from_peak: float,
        notes: str = "",
    ) -> Path:
        """일일 스냅샷을 Vault에 저장

        Args:
            date: 날짜 (YYYY-MM-DD)
            balance: 계좌 잔고
            positions: 보유 포지션 목록
            equity_curve_value: 자산 곡선 값
            dd_from_peak: 고점 대비 드로다운 (%, 음수)
            notes: 추가 메모

        Returns:
            저장된 파일 경로
        """
        lines = [
            "---",
            f"date: {date}",
            "type: daily-snapshot",
            f"equity: {equity_curve_value:.0f}",
            f"dd: {dd_from_peak:.1f}",
            "tags: [trading, daily]",
            "---",
            "",
            f"# Daily Snapshot {date}",
            "",
            "## 계좌 현황",
            "",
            "| 항목 | 값 |",
            "|------|-----|",
            f"| 총 평가 | {_format_krw(balance.total_equity)} |",
            f"| 가용현금 | {_format_krw(balance.available_cash)} |",
            f"| 평가손익 | {_format_signed_krw(balance.total_pnl)} ({_format_pct(balance.total_pnl_percent, multiply=False)}) |",
            f"| DD (고점대비) | {dd_from_peak:.1f}% |",
        ]

        if positions:
            lines.extend([
                "",
                "## 보유종목",
                "",
                "| 종목 | 수량 | 평균가 | 현재가 | 손익 | 손익률 |",
                "|------|------|--------|--------|------|--------|",
            ])
            for pos in positions:
                name = pos.name or pos.symbol
                lines.append(
                    f"| {name} | {pos.quantity:,} | "
                    f"{pos.average_price:,.0f} | {pos.current_price:,.0f} | "
                    f"{_format_signed_krw(pos.unrealized_pnl)} | "
                    f"{_format_pct(pos.unrealized_pnl_percent, multiply=False)} |"
                )

        if notes:
            lines.extend(["", "## 메모", "", notes])

        lines.append("")
        return self._write_file(self.daily_dir / f"{date}.md", "\n".join(lines))

    def write_weekly_report(
        self,
        report: WeeklyReport,
    ) -> Path:
        """주간 복기 리포트를 Vault에 저장

        report.to_markdown()을 본문으로 사용하고, Dataview 호환
        YAML frontmatter를 추가한다.

        Args:
            report: 주간 복기 리포트

        Returns:
            저장된 파일 경로
        """
        dt = datetime.strptime(report.period_end, "%Y-%m-%d")
        iso_cal = dt.isocalendar()
        filename = f"{iso_cal.year}-W{iso_cal.week:02d}.md"

        frontmatter = "\n".join([
            "---",
            f"period: {report.period_start} ~ {report.period_end}",
            "type: weekly-review",
            f"return: {report.portfolio_return * 100:+.1f}",
            f"sharpe: {report.sharpe:.2f}",
            "tags: [trading, weekly, review]",
            "---",
        ])

        content = f"{frontmatter}\n\n{report.to_markdown()}\n"
        return self._write_file(self.weekly_dir / filename, content)

    def write_trade_log(
        self,
        date: str,
        trades: List[TradeRecord],
    ) -> Path:
        """거래 로그를 Vault에 저장

        Args:
            date: 날짜 (YYYY-MM-DD)
            trades: 거래 기록 목록

        Returns:
            저장된 파일 경로
        """
        total_amount = sum(t.amount for t in trades)
        total_cost = sum(t.commission + t.tax + t.slippage for t in trades)

        lines = [
            "---",
            f"date: {date}",
            "type: trade-log",
            f"trade_count: {len(trades)}",
            f"total_amount: {total_amount:.0f}",
            "tags: [trading, trades]",
            "---",
            "",
            f"# 거래 로그 {date}",
            "",
            f"> 총 {len(trades)}건, 거래금액 {_format_krw(total_amount)}, "
            f"비용 {_format_krw(total_cost)}",
            "",
            "| 종목 | 매매 | 수량 | 가격 | 금액 | 수수료 | 세금 | 슬리피지 |",
            "|------|------|------|------|------|--------|------|----------|",
        ]

        for t in trades:
            lines.append(
                f"| {t.ticker} | {t.action} | {t.quantity:,} | "
                f"{t.price:,.0f} | {_format_krw(t.amount)} | "
                f"{t.commission:,.0f} | {t.tax:,.0f} | {t.slippage:,.0f} |"
            )

        lines.append("")
        return self._write_file(
            self.trades_dir / f"{date}-trades.md", "\n".join(lines)
        )

    def append_equity_curve(
        self,
        date: str,
        equity: float,
    ) -> None:
        """자산 곡선 CSV에 한 행 추가

        파일이 없으면 헤더와 함께 생성한다.

        Args:
            date: 날짜 (YYYY-MM-DD)
            equity: 자산 평가액
        """
        csv_path = self._vault / self._area / "equity_curve.csv"
        csv_path.parent.mkdir(parents=True, exist_ok=True)

        file_exists = csv_path.exists() and csv_path.stat().st_size > 0

        buf = io.StringIO()
        writer = csv.writer(buf)

        if not file_exists:
            writer.writerow(["date", "equity"])

        writer.writerow([date, f"{equity:.0f}"])

        with open(csv_path, "a", encoding="utf-8", newline="") as f:
            f.write(buf.getvalue())

        logger.info("자산 곡선 추가: %s, %s -> %s", date, _format_krw(equity), csv_path)

    # ── 내부 메서드 ──────────────────────────────────────────────

    def _write_file(self, path: Path, content: str) -> Path:
        """파일 저장 (디렉토리 자동 생성)

        Args:
            path: 저장 경로
            content: 파일 내용

        Returns:
            저장된 파일 경로
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info("Vault 파일 저장: %s (%d bytes)", path, len(content.encode("utf-8")))
        return path
