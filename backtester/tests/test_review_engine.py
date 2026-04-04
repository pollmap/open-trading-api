"""복기 엔진 테스트"""

import pytest
from kis_backtest.portfolio.review_engine import (
    ReviewEngine,
    TradeRecord,
    KillCondition,
)


class TestReviewEngine:
    def setup_method(self):
        self.engine = ReviewEngine(
            initial_capital=5_000_000,
            benchmark_returns=[0.002] * 5,  # 벤치마크 주간 ~1%
        )

    def test_basic_review(self):
        # 5일 자산 곡선: 500만 → 510만 (2%)
        eq = [5_000_000, 5_020_000, 5_050_000, 5_030_000, 5_080_000, 5_100_000]
        report = self.engine.weekly_review(
            equity_curve=eq,
            period_start="2026-04-01",
            period_end="2026-04-05",
        )
        assert report.portfolio_return == pytest.approx(0.02, rel=0.01)
        assert report.current_equity == 5_100_000

    def test_negative_return_recommendations(self):
        eq = [5_000_000, 4_950_000, 4_900_000, 4_850_000, 4_800_000, 4_750_000]
        report = self.engine.weekly_review(equity_curve=eq)
        assert report.portfolio_return < 0
        assert any("언더퍼폼" in r for r in report.recommendations)

    def test_kill_condition_triggered(self):
        kc = KillCondition(
            description="매출 성장률 10% 미만",
            metric="revenue_growth",
            threshold=0.10,
            current_value=0.05,
            is_triggered=True,
        )
        report = self.engine.weekly_review(
            equity_curve=[5_000_000, 5_050_000],
            kill_conditions=[kc],
        )
        assert report.any_kill_triggered
        assert any("Kill Condition" in r for r in report.recommendations)

    def test_cost_tracking(self):
        trades = [
            TradeRecord(
                date="2026-04-01", ticker="005930", action="BUY",
                quantity=10, price=55000, amount=550000,
                commission=82, tax=0, slippage=27,
            ),
            TradeRecord(
                date="2026-04-03", ticker="005930", action="SELL",
                quantity=10, price=56000, amount=560000,
                commission=84, tax=1120, slippage=28,
            ),
        ]
        report = self.engine.weekly_review(
            equity_curve=[5_000_000, 5_010_000],
            trades=trades,
        )
        assert report.total_trades == 2
        assert report.total_commission == pytest.approx(166)
        assert report.total_tax == pytest.approx(1120)

    def test_drawdown_calculation(self):
        eq = [5_000_000, 5_200_000, 4_800_000, 4_600_000, 4_900_000]
        report = self.engine.weekly_review(equity_curve=eq)
        # 고점 520만 → 저점 460만 = -11.5%
        assert report.max_drawdown < -0.10

    def test_markdown_output(self):
        report = self.engine.weekly_review(
            equity_curve=[5_000_000, 5_100_000],
            period_start="2026-04-01",
            period_end="2026-04-05",
        )
        md = report.to_markdown()
        assert "주간 복기 리포트" in md
        assert "KOSPI200" in md

    def test_cumulative_tracking(self):
        """2주 연속 복기 — 누적 추적"""
        # Week 1
        self.engine.weekly_review(equity_curve=[5_000_000, 5_100_000])
        # Week 2
        report2 = self.engine.weekly_review(equity_curve=[5_100_000, 5_200_000])
        assert report2.cumulative_return == pytest.approx(0.04, rel=0.01)

    def test_factor_contributions(self):
        report = self.engine.weekly_review(
            equity_curve=[5_000_000, 5_100_000],
            factor_contributions={
                "momentum": 0.015,
                "value": -0.005,
                "low_vol": 0.008,
            },
        )
        assert report.factor_contributions["momentum"] == 0.015
        assert "momentum" in report.summary()
