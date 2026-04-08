"""Tests for asymmetric_position module.

Stan Druckenmiller: "It's not about being right or wrong,
but how much you make when you're right."
"""

from __future__ import annotations

import math

import pytest

from kis_backtest.portfolio.asymmetric_position import (
    AsymmetricDesigner,
    AsymmetricPosition,
    AsymmetryType,
    PositionComponent,
    TICKER_BOND_10Y,
    TICKER_GOLD,
    TICKER_INVERSE_2X,
    TICKER_LEVERAGED_2X,
)


# ────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────
@pytest.fixture
def designer() -> AsymmetricDesigner:
    return AsymmetricDesigner()


# ────────────────────────────────────────────────────────────────
# AsymmetryType Enum
# ────────────────────────────────────────────────────────────────
class TestAsymmetryType:
    def test_enum_values(self) -> None:
        assert AsymmetryType.SPOT_ONLY.value == "spot_only"
        assert AsymmetryType.LEVERAGED_ETF.value == "leveraged_etf"
        assert AsymmetryType.INVERSE_ETF.value == "inverse_etf"
        assert AsymmetryType.CRYPTO_CARRY.value == "crypto_carry"
        assert AsymmetryType.BARBELL.value == "barbell"

    def test_str_enum(self) -> None:
        assert isinstance(AsymmetryType.SPOT_ONLY, str)
        assert AsymmetryType.LEVERAGED_ETF == "leveraged_etf"

    def test_all_members_count(self) -> None:
        assert len(AsymmetryType) == 5


# ────────────────────────────────────────────────────────────────
# PositionComponent
# ────────────────────────────────────────────────────────────────
class TestPositionComponent:
    def test_creation(self) -> None:
        comp = PositionComponent(
            instrument="005930",
            direction="long",
            weight=0.5,
            leverage=2.0,
            description="삼성전자 레버리지",
        )
        assert comp.instrument == "005930"
        assert comp.direction == "long"
        assert comp.weight == 0.5
        assert comp.leverage == 2.0

    def test_defaults(self) -> None:
        comp = PositionComponent(instrument="BTC-KRW", direction="short", weight=1.0)
        assert comp.leverage == 1.0
        assert comp.description == ""

    def test_frozen(self) -> None:
        comp = PositionComponent(instrument="X", direction="long", weight=1.0)
        with pytest.raises(AttributeError):
            comp.weight = 0.5  # type: ignore[misc]


# ────────────────────────────────────────────────────────────────
# AsymmetricPosition
# ────────────────────────────────────────────────────────────────
class TestAsymmetricPosition:
    def test_is_asymmetric_true(self) -> None:
        pos = AsymmetricPosition(
            symbol="X",
            name="test",
            asymmetry_type=AsymmetryType.BARBELL,
            capital_at_risk=100,
            potential_upside=500,
            risk_reward_ratio=5.0,
        )
        assert pos.is_asymmetric is True

    def test_is_asymmetric_false(self) -> None:
        pos = AsymmetricPosition(
            symbol="X",
            name="test",
            asymmetry_type=AsymmetryType.SPOT_ONLY,
            capital_at_risk=100,
            potential_upside=150,
            risk_reward_ratio=1.5,
        )
        assert pos.is_asymmetric is False

    def test_is_asymmetric_boundary(self) -> None:
        pos = AsymmetricPosition(
            symbol="X",
            name="test",
            asymmetry_type=AsymmetryType.SPOT_ONLY,
            capital_at_risk=100,
            potential_upside=200,
            risk_reward_ratio=2.0,
        )
        assert pos.is_asymmetric is False  # strictly > 2.0

    def test_summary_finite_upside(self) -> None:
        pos = AsymmetricPosition(
            symbol="005930",
            name="삼성전자 레버리지",
            asymmetry_type=AsymmetryType.LEVERAGED_ETF,
            capital_at_risk=1_000_000,
            potential_upside=3_000_000,
            risk_reward_ratio=3.0,
            description="테스트",
        )
        s = pos.summary()
        assert "005930" in s
        assert "삼성전자 레버리지" in s
        assert "3,000,000" in s
        assert "3.00x" in s
        assert "Yes" in s
        assert "테스트" in s

    def test_summary_infinite_upside(self) -> None:
        pos = AsymmetricPosition(
            symbol="X",
            name="바벨",
            asymmetry_type=AsymmetryType.BARBELL,
            capital_at_risk=100_000,
            potential_upside=float("inf"),
            risk_reward_ratio=float("inf"),
        )
        s = pos.summary()
        assert "무제한" in s

    def test_frozen(self) -> None:
        pos = AsymmetricPosition(
            symbol="X",
            name="test",
            asymmetry_type=AsymmetryType.SPOT_ONLY,
            capital_at_risk=100,
            potential_upside=100,
            risk_reward_ratio=1.0,
        )
        with pytest.raises(AttributeError):
            pos.symbol = "Y"  # type: ignore[misc]

    def test_components_default_empty(self) -> None:
        pos = AsymmetricPosition(
            symbol="X",
            name="test",
            asymmetry_type=AsymmetryType.SPOT_ONLY,
            capital_at_risk=100,
            potential_upside=100,
            risk_reward_ratio=1.0,
        )
        assert pos.components == []


# ────────────────────────────────────────────────────────────────
# AsymmetricDesigner — Leveraged ETF
# ────────────────────────────────────────────────────────────────
class TestDesignLeveragedETF:
    def test_basic_2x(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_leveraged_etf("005930", capital=10_000_000, leverage=2)
        assert pos.asymmetry_type == AsymmetryType.LEVERAGED_ETF
        assert pos.capital_at_risk == 10_000_000
        assert pos.potential_upside == pytest.approx(10_000_000 * 2 * 0.30)
        assert pos.risk_reward_ratio == pytest.approx(0.60)
        assert len(pos.components) == 1
        assert pos.components[0].leverage == 2.0

    def test_3x_leverage(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_leveraged_etf("005930", capital=5_000_000, leverage=3)
        assert pos.potential_upside == pytest.approx(5_000_000 * 3 * 0.30)
        assert pos.components[0].leverage == 3.0

    def test_custom_expected_move(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_leveraged_etf(
            "005930", capital=1_000_000, leverage=2, expected_move=0.50,
        )
        assert pos.potential_upside == pytest.approx(1_000_000 * 2 * 0.50)

    def test_zero_capital_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="capital must be positive"):
            designer.design_leveraged_etf("X", capital=0)

    def test_negative_capital_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="capital must be positive"):
            designer.design_leveraged_etf("X", capital=-100)

    def test_leverage_below_one_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="leverage must be >= 1"):
            designer.design_leveraged_etf("X", capital=1_000_000, leverage=0)

    def test_component_direction(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_leveraged_etf("X", capital=1_000_000)
        assert pos.components[0].direction == "long"
        assert pos.components[0].instrument == TICKER_LEVERAGED_2X


# ────────────────────────────────────────────────────────────────
# AsymmetricDesigner — Crypto Carry
# ────────────────────────────────────────────────────────────────
class TestDesignCryptoCarry:
    def test_basic(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_crypto_carry("BTC-KRW", capital=10_000_000)
        assert pos.asymmetry_type == AsymmetryType.CRYPTO_CARRY
        assert pos.capital_at_risk == pytest.approx(10_000_000 * 0.05)
        assert pos.potential_upside == pytest.approx(10_000_000 * 0.15)
        assert len(pos.components) == 2

    def test_high_funding_rate(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_crypto_carry(
            "ETH-KRW", capital=5_000_000, funding_rate_annual=0.30,
        )
        assert pos.potential_upside == pytest.approx(5_000_000 * 0.30)
        assert pos.risk_reward_ratio == pytest.approx(0.30 / 0.05)

    def test_zero_capital_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="capital must be positive"):
            designer.design_crypto_carry("BTC-KRW", capital=0)

    def test_negative_funding_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="funding_rate_annual must be >= 0"):
            designer.design_crypto_carry("BTC-KRW", capital=1_000_000, funding_rate_annual=-0.05)

    def test_spot_and_futures_legs(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_crypto_carry("BTC-KRW", capital=1_000_000)
        directions = {c.direction for c in pos.components}
        assert directions == {"long", "short"}

    def test_is_asymmetric_at_default_rate(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_crypto_carry("BTC-KRW", capital=1_000_000)
        # 15% / 5% = 3.0 > 2.0
        assert pos.is_asymmetric is True


# ────────────────────────────────────────────────────────────────
# AsymmetricDesigner — Barbell
# ────────────────────────────────────────────────────────────────
class TestDesignBarbell:
    def test_default_weights(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_barbell(capital=10_000_000)
        assert pos.asymmetry_type == AsymmetryType.BARBELL
        assert math.isinf(pos.potential_upside)
        assert math.isinf(pos.risk_reward_ratio)
        assert pos.is_asymmetric is True
        assert len(pos.components) == 2

    def test_max_loss_calculation(self, designer: AsymmetricDesigner) -> None:
        capital = 10_000_000
        pos = designer.design_barbell(capital=capital)
        risky = capital * 0.1
        safe = capital * 0.9
        expected_loss = risky + safe * 0.02
        assert pos.capital_at_risk == pytest.approx(expected_loss)

    def test_custom_weights(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_barbell(safe_weight=0.8, risky_weight=0.2, capital=5_000_000)
        assert len(pos.components) == 2
        weights = [c.weight for c in pos.components]
        assert pytest.approx(0.8) in weights
        assert pytest.approx(0.2) in weights

    def test_zero_capital_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="capital must be positive"):
            designer.design_barbell(capital=0)

    def test_negative_weight_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="weights must be non-negative"):
            designer.design_barbell(safe_weight=-0.1, risky_weight=1.1)

    def test_weights_not_summing_to_one_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="weights must sum to 1.0"):
            designer.design_barbell(safe_weight=0.5, risky_weight=0.3)

    def test_bond_component_ticker(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_barbell(capital=1_000_000)
        instruments = [c.instrument for c in pos.components]
        assert TICKER_BOND_10Y in instruments


# ────────────────────────────────────────────────────────────────
# AsymmetricDesigner — Inverse Hedge
# ────────────────────────────────────────────────────────────────
class TestDesignInverseHedge:
    def test_basic(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_inverse_hedge(portfolio_value=100_000_000)
        assert pos.asymmetry_type == AsymmetryType.INVERSE_ETF
        assert pos.capital_at_risk == pytest.approx(10_000_000)
        # 10M * 2 * 0.2 = 4M
        assert pos.potential_upside == pytest.approx(4_000_000)
        assert pos.risk_reward_ratio == pytest.approx(0.4)

    def test_custom_hedge_pct(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_inverse_hedge(portfolio_value=50_000_000, hedge_pct=0.05)
        assert pos.capital_at_risk == pytest.approx(50_000_000 * 0.05)

    def test_zero_portfolio_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="portfolio_value must be positive"):
            designer.design_inverse_hedge(portfolio_value=0)

    def test_hedge_pct_zero_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="hedge_pct must be in"):
            designer.design_inverse_hedge(portfolio_value=1_000_000, hedge_pct=0)

    def test_hedge_pct_over_one_raises(self, designer: AsymmetricDesigner) -> None:
        with pytest.raises(ValueError, match="hedge_pct must be in"):
            designer.design_inverse_hedge(portfolio_value=1_000_000, hedge_pct=1.5)

    def test_inverse_ticker(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_inverse_hedge(portfolio_value=1_000_000)
        assert pos.components[0].instrument == TICKER_INVERSE_2X
        assert pos.symbol == TICKER_INVERSE_2X


# ────────────────────────────────────────────────────────────────
# AsymmetricDesigner — Evaluate Risk/Reward
# ────────────────────────────────────────────────────────────────
class TestEvaluateRiskReward:
    def test_grade_s(self, designer: AsymmetricDesigner) -> None:
        pos = AsymmetricPosition(
            symbol="X", name="test",
            asymmetry_type=AsymmetryType.BARBELL,
            capital_at_risk=100, potential_upside=float("inf"),
            risk_reward_ratio=float("inf"),
        )
        result = designer.evaluate_risk_reward(pos)
        assert result["grade"] == "S"
        assert result["is_asymmetric"] is True

    def test_grade_a(self, designer: AsymmetricDesigner) -> None:
        pos = AsymmetricPosition(
            symbol="X", name="test",
            asymmetry_type=AsymmetryType.LEVERAGED_ETF,
            capital_at_risk=100, potential_upside=700,
            risk_reward_ratio=7.0,
        )
        assert designer.evaluate_risk_reward(pos)["grade"] == "A"

    def test_grade_b(self, designer: AsymmetricDesigner) -> None:
        pos = AsymmetricPosition(
            symbol="X", name="test",
            asymmetry_type=AsymmetryType.CRYPTO_CARRY,
            capital_at_risk=100, potential_upside=300,
            risk_reward_ratio=3.0,
        )
        assert designer.evaluate_risk_reward(pos)["grade"] == "B"

    def test_grade_c(self, designer: AsymmetricDesigner) -> None:
        pos = AsymmetricPosition(
            symbol="X", name="test",
            asymmetry_type=AsymmetryType.SPOT_ONLY,
            capital_at_risk=100, potential_upside=100,
            risk_reward_ratio=1.0,
        )
        result = designer.evaluate_risk_reward(pos)
        assert result["grade"] == "C"
        assert result["is_asymmetric"] is False

    def test_result_keys(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_leveraged_etf("X", capital=1_000_000)
        result = designer.evaluate_risk_reward(pos)
        expected_keys = {
            "risk_reward_ratio", "is_asymmetric", "capital_at_risk",
            "potential_upside", "max_loss_pct", "num_legs",
            "asymmetry_type", "grade",
        }
        assert set(result.keys()) == expected_keys

    def test_num_legs(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_crypto_carry("BTC-KRW", capital=1_000_000)
        result = designer.evaluate_risk_reward(pos)
        assert result["num_legs"] == 2

    def test_grade_s_at_boundary_10(self, designer: AsymmetricDesigner) -> None:
        pos = AsymmetricPosition(
            symbol="X", name="test",
            asymmetry_type=AsymmetryType.BARBELL,
            capital_at_risk=100, potential_upside=1000,
            risk_reward_ratio=10.0,
        )
        assert designer.evaluate_risk_reward(pos)["grade"] == "S"

    def test_infinite_upside_serialized(self, designer: AsymmetricDesigner) -> None:
        pos = designer.design_barbell(capital=1_000_000)
        result = designer.evaluate_risk_reward(pos)
        assert result["potential_upside"] == "inf"


# ────────────────────────────────────────────────────────────────
# Ticker Constants
# ────────────────────────────────────────────────────────────────
class TestTickerConstants:
    def test_leveraged(self) -> None:
        assert TICKER_LEVERAGED_2X == "252710"

    def test_inverse(self) -> None:
        assert TICKER_INVERSE_2X == "252670"

    def test_bond(self) -> None:
        assert TICKER_BOND_10Y == "148070"

    def test_gold(self) -> None:
        assert TICKER_GOLD == "132030"
