"""레짐 기반 자산배분기 테스트 — Druckenmiller 스타일 포트폴리오 조정"""

from __future__ import annotations

from datetime import datetime

import pytest

from kis_backtest.portfolio.macro_regime import REGIME_ALLOCATION, Regime
from kis_backtest.portfolio.regime_allocator import (
    DEFAULT_ETF_MAP,
    AllocationPlan,
    AllocationTarget,
    AssetClass,
    RebalanceOrder,
    RegimeAllocator,
    _asset_description,
)


# ── AssetClass 테스트 ────────────────────────────────────────


class TestAssetClass:
    def test_all_values(self):
        expected = {"equity", "bond", "gold", "crypto", "cash", "inverse"}
        assert {ac.value for ac in AssetClass} == expected

    def test_str_enum(self):
        assert AssetClass.EQUITY == "equity"
        assert isinstance(AssetClass.BOND, str)

    def test_from_string(self):
        assert AssetClass("equity") is AssetClass.EQUITY
        assert AssetClass("inverse") is AssetClass.INVERSE

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            AssetClass("stocks")


# ── DEFAULT_ETF_MAP 테스트 ───────────────────────────────────


class TestDefaultETFMap:
    def test_equity_ticker(self):
        assert DEFAULT_ETF_MAP[AssetClass.EQUITY] == "069500"

    def test_bond_ticker(self):
        assert DEFAULT_ETF_MAP[AssetClass.BOND] == "148070"

    def test_gold_ticker(self):
        assert DEFAULT_ETF_MAP[AssetClass.GOLD] == "132030"

    def test_crypto_not_available(self):
        assert DEFAULT_ETF_MAP[AssetClass.CRYPTO] == "NOT_AVAILABLE"

    def test_cash_is_cash(self):
        assert DEFAULT_ETF_MAP[AssetClass.CASH] == "CASH"

    def test_inverse_ticker(self):
        assert DEFAULT_ETF_MAP[AssetClass.INVERSE] == "252670"

    def test_all_asset_classes_mapped(self):
        for ac in AssetClass:
            assert ac in DEFAULT_ETF_MAP


# ── AllocationTarget 테스트 ──────────────────────────────────


class TestAllocationTarget:
    def test_frozen(self):
        t = AllocationTarget(asset_class=AssetClass.EQUITY, weight=0.7)
        with pytest.raises(AttributeError):
            t.weight = 0.5  # type: ignore[misc]

    def test_defaults(self):
        t = AllocationTarget(asset_class=AssetClass.CASH, weight=0.1)
        assert t.etf_ticker is None
        assert t.description == ""

    def test_with_all_fields(self):
        t = AllocationTarget(
            asset_class=AssetClass.GOLD,
            weight=0.2,
            etf_ticker="132030",
            description="금 헤지",
        )
        assert t.asset_class is AssetClass.GOLD
        assert t.weight == 0.2
        assert t.etf_ticker == "132030"
        assert t.description == "금 헤지"


# ── RebalanceOrder 테스트 ────────────────────────────────────


class TestRebalanceOrder:
    def test_frozen(self):
        order = RebalanceOrder(
            asset_class=AssetClass.EQUITY,
            etf_ticker="069500",
            action="buy",
            amount=10_000_000,
            reason="test",
        )
        with pytest.raises(AttributeError):
            order.amount = 0  # type: ignore[misc]

    def test_buy_order(self):
        order = RebalanceOrder(
            asset_class=AssetClass.BOND,
            etf_ticker="148070",
            action="buy",
            amount=50_000_000,
            reason="수축기 채권 확대",
        )
        assert order.action == "buy"
        assert order.amount == 50_000_000

    def test_sell_order(self):
        order = RebalanceOrder(
            asset_class=AssetClass.EQUITY,
            etf_ticker="069500",
            action="sell",
            amount=30_000_000,
            reason="위기 시 주식 축소",
        )
        assert order.action == "sell"


# ── AllocationPlan 테스트 ────────────────────────────────────


class TestAllocationPlan:
    @pytest.fixture()
    def expansion_plan(self) -> AllocationPlan:
        return AllocationPlan(
            regime=Regime.EXPANSION,
            targets=(
                AllocationTarget(AssetClass.EQUITY, 0.7, "069500"),
                AllocationTarget(AssetClass.CRYPTO, 0.2, "NOT_AVAILABLE"),
                AllocationTarget(AssetClass.CASH, 0.1, "CASH"),
            ),
            total_capital=100_000_000,
            created_at="2026-04-08 10:00:00",
        )

    def test_frozen(self, expansion_plan: AllocationPlan):
        with pytest.raises(AttributeError):
            expansion_plan.total_capital = 0  # type: ignore[misc]

    def test_weights_sum(self, expansion_plan: AllocationPlan):
        assert abs(expansion_plan.weights_sum - 1.0) < 0.01

    def test_weights_sum_partial(self):
        plan = AllocationPlan(
            regime=Regime.CRISIS,
            targets=(
                AllocationTarget(AssetClass.CASH, 0.5),
                AllocationTarget(AssetClass.GOLD, 0.3),
            ),
            total_capital=10_000_000,
            created_at="2026-01-01",
        )
        assert abs(plan.weights_sum - 0.8) < 0.001

    def test_summary_contains_regime(self, expansion_plan: AllocationPlan):
        s = expansion_plan.summary()
        assert "EXPANSION" in s

    def test_summary_contains_capital(self, expansion_plan: AllocationPlan):
        s = expansion_plan.summary()
        assert "100,000,000" in s

    def test_summary_contains_assets(self, expansion_plan: AllocationPlan):
        s = expansion_plan.summary()
        assert "equity" in s
        assert "crypto" in s
        assert "cash" in s

    def test_to_orders_buy(self, expansion_plan: AllocationPlan):
        # 현금만 보유 → 전부 매수
        holdings = {"CASH": 100_000_000}
        orders = expansion_plan.to_orders(holdings)
        buy_orders = [o for o in orders if o.action == "buy"]
        assert len(buy_orders) >= 2  # equity, crypto

    def test_to_orders_sell(self, expansion_plan: AllocationPlan):
        # 주식에 올인 → 일부 매도
        holdings = {"069500": 100_000_000}
        orders = expansion_plan.to_orders(holdings)
        sell_orders = [o for o in orders if o.action == "sell"]
        assert len(sell_orders) >= 1  # equity 초과분 매도

    def test_to_orders_no_change(self, expansion_plan: AllocationPlan):
        # 이미 목표 배분과 동일
        holdings = {
            "069500": 70_000_000,
            "NOT_AVAILABLE": 20_000_000,
            "CASH": 10_000_000,
        }
        orders = expansion_plan.to_orders(holdings)
        assert len(orders) == 0

    def test_to_orders_amounts(self, expansion_plan: AllocationPlan):
        holdings = {"CASH": 100_000_000}
        orders = expansion_plan.to_orders(holdings)
        equity_order = next(o for o in orders if o.asset_class == AssetClass.EQUITY)
        assert equity_order.amount == pytest.approx(70_000_000, abs=1)
        assert equity_order.action == "buy"


# ── RegimeAllocator 테스트 ───────────────────────────────────


class TestRegimeAllocatorInit:
    def test_default_init(self):
        allocator = RegimeAllocator(total_capital=100_000_000)
        assert allocator.total_capital == 100_000_000

    def test_negative_capital_raises(self):
        with pytest.raises(ValueError, match="positive"):
            RegimeAllocator(total_capital=-1)

    def test_zero_capital_raises(self):
        with pytest.raises(ValueError, match="positive"):
            RegimeAllocator(total_capital=0)

    def test_custom_allocations(self):
        custom = {
            Regime.EXPANSION: {"equity": 0.5, "cash": 0.5},
        }
        allocator = RegimeAllocator(total_capital=50_000_000, custom_allocations=custom)
        assert allocator.allocations[Regime.EXPANSION] == {"equity": 0.5, "cash": 0.5}

    def test_default_allocations_match_macro_regime(self):
        allocator = RegimeAllocator(total_capital=100_000_000)
        for regime in Regime:
            assert allocator.allocations[regime] == REGIME_ALLOCATION[regime]


class TestRegimeAllocatorAllocate:
    @pytest.fixture()
    def allocator(self) -> RegimeAllocator:
        return RegimeAllocator(total_capital=100_000_000)

    def test_expansion_plan(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.EXPANSION)
        assert plan.regime is Regime.EXPANSION
        assert abs(plan.weights_sum - 1.0) < 0.01

    def test_contraction_plan(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.CONTRACTION)
        assert plan.regime is Regime.CONTRACTION
        weights = {t.asset_class.value: t.weight for t in plan.targets}
        assert weights["bond"] == pytest.approx(0.5)

    def test_crisis_plan(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.CRISIS)
        weights = {t.asset_class.value: t.weight for t in plan.targets}
        assert weights["cash"] == pytest.approx(0.7)
        assert weights["inverse"] == pytest.approx(0.1)

    def test_recovery_plan(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.RECOVERY)
        weights = {t.asset_class.value: t.weight for t in plan.targets}
        assert weights["equity"] == pytest.approx(0.5)
        assert weights["crypto"] == pytest.approx(0.15)

    def test_all_regimes_sum_to_one(self, allocator: RegimeAllocator):
        for regime in Regime:
            plan = allocator.allocate(regime)
            assert abs(plan.weights_sum - 1.0) < 0.01, (
                f"{regime.value}: weights_sum={plan.weights_sum}"
            )

    def test_etf_tickers_assigned(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.EXPANSION)
        for target in plan.targets:
            assert target.etf_ticker is not None

    def test_descriptions_non_empty(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.CRISIS)
        for target in plan.targets:
            assert len(target.description) > 0

    def test_total_capital_preserved(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.EXPANSION)
        assert plan.total_capital == 100_000_000

    def test_created_at_format(self, allocator: RegimeAllocator):
        plan = allocator.allocate(Regime.EXPANSION)
        # YYYY-MM-DD HH:MM:SS
        datetime.strptime(plan.created_at, "%Y-%m-%d %H:%M:%S")


class TestRegimeAllocatorRebalance:
    @pytest.fixture()
    def allocator(self) -> RegimeAllocator:
        return RegimeAllocator(total_capital=100_000_000)

    def test_from_cash_to_expansion(self, allocator: RegimeAllocator):
        orders = allocator.rebalance(
            Regime.EXPANSION,
            current_holdings={"CASH": 100_000_000},
        )
        buy_tickers = {o.etf_ticker for o in orders if o.action == "buy"}
        assert "069500" in buy_tickers  # equity

    def test_from_expansion_to_crisis(self, allocator: RegimeAllocator):
        # CRISIS: cash=70%, gold=20%, inverse=10%
        # 기존 보유: CASH 10M → 목표 70M (buy 60M)
        holdings = {
            "069500": 70_000_000,
            "NOT_AVAILABLE": 20_000_000,
            "CASH": 10_000_000,
        }
        orders = allocator.rebalance(Regime.CRISIS, current_holdings=holdings)
        # CRISIS에 equity 배분이 없으므로 equity 매도 주문은 생성 안 됨
        # 대신 cash 매수, gold 매수, inverse 매수 주문이 생성
        cash_buy = [o for o in orders if o.etf_ticker == "CASH" and o.action == "buy"]
        assert len(cash_buy) == 1
        assert cash_buy[0].amount == pytest.approx(60_000_000, abs=1)

    def test_empty_holdings(self, allocator: RegimeAllocator):
        orders = allocator.rebalance(Regime.RECOVERY, current_holdings={})
        total_buy = sum(o.amount for o in orders if o.action == "buy")
        assert total_buy == pytest.approx(100_000_000, abs=100)

    def test_orders_have_reasons(self, allocator: RegimeAllocator):
        orders = allocator.rebalance(
            Regime.CONTRACTION,
            current_holdings={"CASH": 100_000_000},
        )
        for order in orders:
            assert len(order.reason) > 0
            assert order.asset_class.value in order.reason


class TestTransitionSummary:
    @pytest.fixture()
    def allocator(self) -> RegimeAllocator:
        return RegimeAllocator(total_capital=100_000_000)

    def test_expansion_to_crisis(self, allocator: RegimeAllocator):
        summary = allocator.transition_summary(Regime.EXPANSION, Regime.CRISIS)
        assert "EXPANSION" in summary
        assert "CRISIS" in summary

    def test_contains_capital(self, allocator: RegimeAllocator):
        summary = allocator.transition_summary(Regime.EXPANSION, Regime.CONTRACTION)
        assert "100,000,000" in summary

    def test_contains_assets(self, allocator: RegimeAllocator):
        summary = allocator.transition_summary(Regime.EXPANSION, Regime.CONTRACTION)
        assert "equity" in summary

    def test_same_regime(self, allocator: RegimeAllocator):
        summary = allocator.transition_summary(Regime.EXPANSION, Regime.EXPANSION)
        assert "EXPANSION" in summary
        # 변화 없음
        for line in summary.split("\n")[5:]:
            if line.strip() and "자산" not in line and "---" not in line:
                assert "+0%" in line.replace(" ", "")


# ── _asset_description 유틸 테스트 ───────────────────────────


class TestAssetDescription:
    def test_known_combination(self):
        desc = _asset_description(AssetClass.EQUITY, Regime.EXPANSION)
        assert "확장기" in desc
        assert "주식" in desc

    def test_crisis_cash(self):
        desc = _asset_description(AssetClass.CASH, Regime.CRISIS)
        assert "위기" in desc
        assert "현금" in desc

    def test_unknown_combination_fallback(self):
        # EQUITY + CRISIS 는 descriptions에 있음
        # BOND + EXPANSION 은 없음 → fallback
        desc = _asset_description(AssetClass.BOND, Regime.EXPANSION)
        assert "bond" in desc
        assert "expansion" in desc
