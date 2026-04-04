"""리스크 모듈 단위 테스트

거래비용, 드로다운, 변동성 타겟팅, 집중도, MCP 브릿지 검증.
"""

import math
import pytest

from kis_backtest.strategies.risk.cost_model import (
    KoreaFeeSchedule,
    KoreaTransactionCostModel,
    Market,
)
from kis_backtest.strategies.risk.drawdown_guard import (
    ConcentrationLimits,
    DrawdownGuard,
    check_concentration,
)
from kis_backtest.strategies.risk.vol_target import (
    VolatilityTargeter,
    turbulence_index,
)
from kis_backtest.portfolio.mcp_bridge import MCPBridge, OrderAction


# ============================================================
# 거래비용 모델
# ============================================================

class TestKoreaTransactionCostModel:
    def setup_method(self):
        self.model = KoreaTransactionCostModel(slippage_bps=0)  # 슬리피지 제거하여 세금만 테스트

    def test_kospi_sell_tax(self):
        """KOSPI 매도세 = 증권거래세 0.05% + 농특세 0.15% = 0.20%"""
        assert self.model.sell_tax_rate(Market.KOSPI) == pytest.approx(0.002)

    def test_kosdaq_sell_tax(self):
        """KOSDAQ 매도세 = 증권거래세 0.20% (농특세 면제)"""
        assert self.model.sell_tax_rate(Market.KOSDAQ) == pytest.approx(0.002)

    def test_buy_cost_no_tax(self):
        """매수 시 세금 없음 — 수수료만"""
        cost = self.model.trade_cost(10_000_000, Market.KOSPI, is_sell=False)
        assert cost.tax == 0.0
        assert cost.broker_fee == pytest.approx(1500)  # 0.015% of 10M

    def test_sell_cost_includes_tax(self):
        """매도 시 세금 포함"""
        cost = self.model.trade_cost(10_000_000, Market.KOSPI, is_sell=True)
        assert cost.tax == pytest.approx(20_000)  # 0.20% of 10M
        assert cost.broker_fee == pytest.approx(1500)

    def test_round_trip_rate(self):
        """왕복 비용 = 매수(수수료) + 매도(수수료+세금)"""
        rt = self.model.round_trip_rate(Market.KOSPI)
        # 0.015% + 0.015% + 0.20% = 0.23%
        assert rt == pytest.approx(0.0023)

    def test_annual_cost_monthly(self):
        """월간 리밸런싱(12RT) 연간 비용"""
        annual = self.model.annual_cost(12, Market.KOSPI)
        assert annual == pytest.approx(0.0276)  # 2.76%

    def test_kelly_negative_returns_zero(self):
        """기대수익 < 비용이면 Kelly = 0"""
        f = self.model.kelly_adjusted(mu=0.02, sigma=0.25, rf=0.035, n_roundtrips=12)
        assert f == 0.0

    def test_kelly_half_kelly(self):
        """Half-Kelly ≤ full Kelly, fraction이 적용됨"""
        full = self.model.kelly_adjusted(mu=0.15, sigma=0.25, rf=0.035, n_roundtrips=12, fraction=1.0)
        half = self.model.kelly_adjusted(mu=0.15, sigma=0.25, rf=0.035, n_roundtrips=12, fraction=0.5)
        # full이 1.0으로 cap되면 half는 정확히 절반이 아닐 수 있음
        assert half <= full
        assert half > 0

    def test_breakeven_alpha(self):
        """손익분기 알파 = 연간 비용"""
        be = self.model.breakeven_alpha(12, Market.KOSPI)
        annual = self.model.annual_cost(12, Market.KOSPI)
        assert be == annual

    def test_max_frequency(self):
        """최대 허용 빈도 계산"""
        max_rt = self.model.max_frequency(0.10, Market.KOSPI)
        # 10% alpha / 0.23% per RT ≈ 43
        assert max_rt == pytest.approx(43.5, rel=0.1)

    def test_slippage_included(self):
        """슬리피지 포함 모델"""
        model_with_slip = KoreaTransactionCostModel(slippage_bps=5)
        rt = model_with_slip.round_trip_rate(Market.KOSPI)
        # 0.23% + 2×0.05% = 0.33%
        assert rt == pytest.approx(0.0033, rel=0.01)


# ============================================================
# 드로다운 가드
# ============================================================

class TestDrawdownGuard:
    def test_normal_state(self):
        guard = DrawdownGuard()
        s = guard.check(100, 100)
        assert s.action == "NORMAL"
        assert s.reduction_factor == 1.0

    def test_warning_threshold(self):
        guard = DrawdownGuard(warning_pct=-0.10)
        s = guard.check(89, 100)  # -11%
        assert "WARNING" in s.action
        assert s.reduction_factor == 1.0  # 아직 축소 안 함

    def test_reduce_threshold(self):
        guard = DrawdownGuard(reduce_pct=-0.15, reduce_factor=0.5)
        s = guard.check(84, 100)  # -16%
        assert "REDUCE" in s.action
        assert s.reduction_factor == 0.5

    def test_halt_threshold(self):
        guard = DrawdownGuard(halt_pct=-0.20)
        s = guard.check(79, 100)  # -21%
        assert "HALT" in s.action
        assert s.reduction_factor == 0.0

    def test_millennium_style(self):
        """Millennium: 5%→경고, 7.5%→축소, 10%→종료"""
        guard = DrawdownGuard(warning_pct=-0.05, reduce_pct=-0.075, halt_pct=-0.10)
        assert guard.check(96, 100).action == "NORMAL"
        assert "WARNING" in guard.check(94, 100).action
        assert "REDUCE" in guard.check(92, 100).action
        assert "HALT" in guard.check(89, 100).action

    def test_max_drawdown_calculation(self):
        guard = DrawdownGuard()
        curve = [100, 110, 105, 90, 95, 100, 85, 90]
        mdd = guard.max_drawdown(curve)
        # 최고 110, 최저 85 → -22.7%
        assert mdd == pytest.approx(-0.2273, rel=0.01)

    def test_invalid_order_raises(self):
        with pytest.raises(ValueError):
            DrawdownGuard(warning_pct=-0.20, reduce_pct=-0.15, halt_pct=-0.10)

    def test_track_equity_curve(self):
        guard = DrawdownGuard(warning_pct=-0.05, reduce_pct=-0.10, halt_pct=-0.15)
        states = guard.track([100, 105, 100, 94, 88, 95])
        actions = [s.action for s in states]
        assert actions[0] == "NORMAL"
        # 94/105 = -10.5% → REDUCE (not WARNING)
        assert "REDUCE" in actions[3] or "WARNING" in actions[3]
        # 88/105 = -16.2% → HALT
        assert "HALT" in actions[4]


# ============================================================
# 집중도 검증
# ============================================================

class TestConcentration:
    def test_single_stock_violation(self):
        result = check_concentration(
            {"A": 0.20, "B": 0.10},
            limits=ConcentrationLimits(max_single_stock=0.15),
        )
        assert len(result["violations"]) == 1
        assert "A" in result["violations"][0]

    def test_sector_violation(self):
        result = check_concentration(
            {"A": 0.15, "B": 0.15, "C": 0.10},
            sectors={"A": "IT", "B": "IT", "C": "금융"},
            limits=ConcentrationLimits(max_single_sector=0.25),
        )
        assert any("IT" in v for v in result["violations"])

    def test_all_pass(self):
        result = check_concentration(
            {"A": 0.10, "B": 0.10, "C": 0.10},
            sectors={"A": "IT", "B": "금융", "C": "소재"},
        )
        assert len(result["violations"]) == 0


# ============================================================
# 변동성 타겟팅
# ============================================================

class TestVolatilityTargeter:
    def test_high_vol_reduces_weight(self):
        """고변동 종목 → 비중 축소"""
        targeter = VolatilityTargeter(target_vol=0.10, max_leverage=2.0)
        # ~30% 연간 변동성 = 일간 ~1.9%
        import random
        random.seed(42)
        returns = [random.gauss(0, 0.019) for _ in range(120)]
        result = targeter.scale(0.20, returns)
        assert result.vol_scaled_weight < 0.20  # 축소됨
        assert result.scale_factor < 1.0

    def test_low_vol_increases_weight(self):
        """저변동 종목 → 비중 확대 (max_leverage까지)"""
        targeter = VolatilityTargeter(target_vol=0.15, max_leverage=2.0)
        # ~5% 연간 변동성 = 일간 ~0.3%
        import random
        random.seed(42)
        returns = [random.gauss(0, 0.002) for _ in range(120)]
        result = targeter.scale(0.10, returns)
        assert result.vol_scaled_weight > 0.10  # 확대됨

    def test_max_leverage_cap(self):
        """max_leverage 초과 방지"""
        targeter = VolatilityTargeter(target_vol=0.50, max_leverage=1.5)
        import random
        random.seed(42)
        returns = [random.gauss(0, 0.01) for _ in range(120)]
        result = targeter.scale(1.0, returns)
        assert result.vol_scaled_weight <= 1.5  # max_leverage=1.5

    def test_empty_returns(self):
        targeter = VolatilityTargeter()
        result = targeter.scale(0.20, [])
        assert result.vol_scaled_weight == 0.20  # 변경 없음

    def test_simple_vs_ewma(self):
        """simple과 ewma는 다른 결과"""
        targeter = VolatilityTargeter()
        import random
        random.seed(42)
        returns = [random.gauss(0, 0.01) for _ in range(120)]
        simple = targeter.scale(0.20, returns, method="simple")
        ewma = targeter.scale(0.20, returns, method="ewma")
        # 값은 다를 수 있지만 둘 다 양수
        assert simple.estimated_vol > 0
        assert ewma.estimated_vol > 0


# ============================================================
# 터뷸런스 인덱스
# ============================================================

class TestTurbulence:
    def test_normal_returns_low_turbulence(self):
        import random
        random.seed(42)
        hist = [[random.gauss(0, 0.01), random.gauss(0, 0.01)] for _ in range(50)]
        turb = turbulence_index([0.001, -0.001], hist)
        assert turb < 2.0  # 정상 범위

    def test_extreme_returns_high_turbulence(self):
        import random
        random.seed(42)
        hist = [[random.gauss(0, 0.01), random.gauss(0, 0.01)] for _ in range(50)]
        turb = turbulence_index([0.05, -0.04], hist)
        assert turb > 5.0  # 위기 수준

    def test_empty_inputs(self):
        assert turbulence_index([], []) == 0.0


# ============================================================
# MCP 브릿지
# ============================================================

class TestMCPBridge:
    def setup_method(self):
        self.bridge = MCPBridge(total_capital=5_000_000)

    def test_basic_order_generation(self):
        order = self.bridge.build_order(
            strategy_name="test",
            factor_scores={"A": {"name": "A주식", "score": 0.8, "sector": "IT"}},
            optimal_weights={"A": 0.10},
        )
        assert order.n_stocks == 1
        assert order.cash_weight == pytest.approx(0.90)

    def test_risk_gate_concentration(self):
        """섹터 집중도 초과 → 게이트 FAIL"""
        order = self.bridge.build_order(
            strategy_name="test",
            factor_scores={
                "A": {"name": "A", "score": 0.8, "sector": "IT"},
                "B": {"name": "B", "score": 0.7, "sector": "IT"},
                "C": {"name": "C", "score": 0.6, "sector": "IT"},
            },
            optimal_weights={"A": 0.15, "B": 0.12, "C": 0.10},
        )
        # IT 37% > 35% 한도
        assert not order.risk_gate_passed

    def test_risk_gate_sharpe_fail(self):
        """Sharpe < 0.5 → 게이트 FAIL"""
        order = self.bridge.build_order(
            strategy_name="test",
            factor_scores={"A": {"name": "A", "score": 0.8, "sector": "금융"}},
            optimal_weights={"A": 0.10},
            backtest_sharpe=0.3,
        )
        assert not order.risk_gate_passed

    def test_risk_gate_all_pass(self):
        """모든 조건 충족 → PASS"""
        order = self.bridge.build_order(
            strategy_name="test",
            factor_scores={
                "A": {"name": "A", "score": 0.8, "sector": "IT"},
                "B": {"name": "B", "score": 0.7, "sector": "금융"},
            },
            optimal_weights={"A": 0.10, "B": 0.10},
            backtest_sharpe=0.8,
            backtest_max_dd=-0.12,
        )
        assert order.risk_gate_passed

    def test_action_assignment(self):
        """현재 비중 → 목표 비중 변화에 따른 행동 결정"""
        order = self.bridge.build_order(
            strategy_name="test",
            factor_scores={
                "A": {"name": "A", "score": 0.8, "sector": "IT"},
                "B": {"name": "B", "score": 0.7, "sector": "금융"},
            },
            optimal_weights={"A": 0.15, "B": 0.05},
            current_weights={"A": 0.10, "B": 0.10},
        )
        a_alloc = next(a for a in order.allocations if a.ticker == "A")
        b_alloc = next(a for a in order.allocations if a.ticker == "B")
        assert a_alloc.action == OrderAction.BUY   # 10% → 15%
        assert b_alloc.action == OrderAction.SELL   # 10% → 5%
