"""Ackman-Druckenmiller 통합 엔진 테스트"""

from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from kis_backtest.portfolio.ackman_druckenmiller import (
    BASE_WEIGHT_PER_SYMBOL,
    REGIME_CASH_FLOOR,
    REGIME_WEIGHT_MULTIPLIER,
    AckmanDruckenmillerEngine,
    InvestmentDecision,
    PortfolioDecision,
)
from kis_backtest.portfolio.catalyst_tracker import CatalystScore, CatalystTracker
from kis_backtest.portfolio.macro_regime import (
    MacroRegimeDashboard,
    Regime,
    RegimeResult,
)


# ── 헬퍼 ─────────────────────────────────────────────────────


def _make_catalyst_score(
    symbol: str = "005930",
    total: float = 3.0,
    positive_score: float = 3.0,
    negative_score: float = 0.0,
    catalyst_count: int = 1,
    top_catalyst: str = "HBM4 양산",
    urgency: str = "near",
) -> CatalystScore:
    return CatalystScore(
        symbol=symbol,
        total=total,
        positive_score=positive_score,
        negative_score=negative_score,
        catalyst_count=catalyst_count,
        top_catalyst=top_catalyst,
        urgency=urgency,
    )


def _make_regime_result(
    regime: Regime = Regime.EXPANSION,
    confidence: float = 0.8,
    score: float = 5.0,
) -> RegimeResult:
    return RegimeResult(
        regime=regime,
        confidence=confidence,
        score=score,
        positive_signals=6,
        negative_signals=1,
        neutral_signals=3,
        allocation={"equity": 0.7, "cash": 0.3},
    )


def _make_engine(
    catalyst_score: CatalystScore | None = None,
    regime_result: RegimeResult | None = None,
    data_dir: str | None = None,
) -> AckmanDruckenmillerEngine:
    """Mock tracker + dashboard로 엔진 생성"""
    tracker = MagicMock(spec=CatalystTracker)
    dashboard = MagicMock(spec=MacroRegimeDashboard)

    if catalyst_score is None:
        catalyst_score = _make_catalyst_score()
    if regime_result is None:
        regime_result = _make_regime_result()

    tracker.score.return_value = catalyst_score
    dashboard.classify_regime.return_value = regime_result

    return AckmanDruckenmillerEngine(tracker, dashboard, data_dir=data_dir)


# ── InvestmentDecision 테스트 ───────────────────────────────


class TestInvestmentDecision:
    def test_frozen(self):
        d = InvestmentDecision(
            symbol="005930",
            action="buy",
            conviction=8.0,
            catalyst_score=3.5,
            regime="expansion",
            regime_weight_adjustment=1.2,
            final_weight=0.096,
            reasoning=["test"],
        )
        with pytest.raises(AttributeError):
            d.action = "sell"  # type: ignore[misc]

    def test_summary_contains_key_info(self):
        d = InvestmentDecision(
            symbol="005930",
            action="buy",
            conviction=8.0,
            catalyst_score=3.5,
            regime="expansion",
            regime_weight_adjustment=1.2,
            final_weight=0.096,
            reasoning=["strong catalyst"],
        )
        s = d.summary()
        assert "005930" in s
        assert "BUY" in s
        assert "8.0" in s
        assert "expansion" in s
        assert "strong catalyst" in s

    def test_summary_skip(self):
        d = InvestmentDecision(
            symbol="000660",
            action="skip",
            conviction=5.0,
            catalyst_score=0.5,
            regime="expansion",
            regime_weight_adjustment=1.2,
            final_weight=0.0,
            reasoning=["no catalyst"],
        )
        assert "SKIP" in d.summary()

    def test_summary_hold(self):
        d = InvestmentDecision(
            symbol="000660",
            action="hold",
            conviction=5.0,
            catalyst_score=1.5,
            regime="recovery",
            regime_weight_adjustment=1.0,
            final_weight=0.0,
            reasoning=[],
        )
        assert "HOLD" in d.summary()

    def test_summary_sell(self):
        d = InvestmentDecision(
            symbol="000660",
            action="sell",
            conviction=3.0,
            catalyst_score=1.5,
            regime="contraction",
            regime_weight_adjustment=0.6,
            final_weight=0.0,
            reasoning=["low conviction"],
        )
        assert "SELL" in d.summary()

    def test_default_reasoning_empty(self):
        d = InvestmentDecision(
            symbol="X",
            action="hold",
            conviction=5.0,
            catalyst_score=1.0,
            regime="recovery",
            regime_weight_adjustment=1.0,
            final_weight=0.0,
        )
        assert d.reasoning == []

    def test_summary_no_reasoning(self):
        d = InvestmentDecision(
            symbol="X",
            action="hold",
            conviction=5.0,
            catalyst_score=1.0,
            regime="recovery",
            regime_weight_adjustment=1.0,
            final_weight=0.0,
        )
        assert "N/A" in d.summary()


# ── PortfolioDecision 테스트 ────────────────────────────────


class TestPortfolioDecision:
    def test_frozen(self):
        p = PortfolioDecision(
            regime=Regime.EXPANSION,
            regime_confidence=0.8,
            decisions=[],
            total_equity_weight=0.7,
            cash_weight=0.3,
        )
        with pytest.raises(AttributeError):
            p.regime = Regime.CRISIS  # type: ignore[misc]

    def test_summary_contains_regime_and_weights(self):
        dec = InvestmentDecision(
            symbol="005930",
            action="buy",
            conviction=8.0,
            catalyst_score=3.5,
            regime="expansion",
            regime_weight_adjustment=1.2,
            final_weight=0.096,
            reasoning=["test"],
        )
        p = PortfolioDecision(
            regime=Regime.EXPANSION,
            regime_confidence=0.8,
            decisions=[dec],
            total_equity_weight=0.096,
            cash_weight=0.904,
        )
        s = p.summary()
        assert "EXPANSION" in s
        assert "005930" in s

    def test_summary_empty_decisions(self):
        p = PortfolioDecision(
            regime=Regime.CRISIS,
            regime_confidence=0.5,
            decisions=[],
            total_equity_weight=0.0,
            cash_weight=1.0,
        )
        s = p.summary()
        assert "CRISIS" in s

    def test_created_at_auto(self):
        p = PortfolioDecision(
            regime=Regime.RECOVERY,
            regime_confidence=0.6,
            decisions=[],
            total_equity_weight=0.0,
            cash_weight=1.0,
        )
        assert len(p.created_at) > 0


# ── 행동 결정 로직 테스트 ────────────────────────────────────


class TestDetermineAction:
    """AckmanDruckenmillerEngine._determine_action 단위 테스트"""

    def test_skip_when_no_catalyst(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=0.5,
            conviction=8.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "skip"
        assert any("no catalyst" in r.lower() for r in reasoning)

    def test_skip_zero_catalyst(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=0.0,
            conviction=9.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "skip"

    def test_sell_low_conviction(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=3.0,
            conviction=3.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "sell"
        assert any("conviction" in r.lower() for r in reasoning)

    def test_sell_very_low_conviction(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=5.0,
            conviction=1.0,
            regime=Regime.RECOVERY,
            reasoning=reasoning,
        )
        assert action == "sell"

    def test_buy_all_conditions_met(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=2.5,
            conviction=7.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "buy"
        assert any("BUY" in r for r in reasoning)

    def test_buy_recovery(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=3.0,
            conviction=8.0,
            regime=Regime.RECOVERY,
            reasoning=reasoning,
        )
        assert action == "buy"

    def test_no_buy_in_crisis_even_with_catalyst(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=5.0,
            conviction=9.0,
            regime=Regime.CRISIS,
            reasoning=reasoning,
        )
        assert action == "hold"

    def test_hold_moderate_catalyst_moderate_conviction(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=1.5,
            conviction=5.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "hold"

    def test_hold_high_catalyst_low_conviction(self):
        """catalyst >= 2.0 but conviction < 6 → hold"""
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=3.0,
            conviction=5.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "hold"

    def test_skip_priority_over_sell(self):
        """catalyst < 1.0 이면 conviction 낮아도 skip (sell 아님)"""
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=0.3,
            conviction=2.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "skip"

    def test_buy_boundary_catalyst_2(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=2.0,
            conviction=6.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "buy"

    def test_hold_boundary_catalyst_below_2(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=1.99,
            conviction=6.0,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "hold"

    def test_sell_boundary_conviction_below_4(self):
        reasoning: list[str] = []
        action = AckmanDruckenmillerEngine._determine_action(
            catalyst_score=3.0,
            conviction=3.99,
            regime=Regime.EXPANSION,
            reasoning=reasoning,
        )
        assert action == "sell"


# ── conviction_to_weight 테스트 ─────────────────────────────


class TestConvictionToWeight:
    def test_max_conviction(self):
        w = AckmanDruckenmillerEngine._conviction_to_weight(10.0)
        assert w == BASE_WEIGHT_PER_SYMBOL

    def test_min_conviction(self):
        w = AckmanDruckenmillerEngine._conviction_to_weight(1.0)
        assert w == round(BASE_WEIGHT_PER_SYMBOL * 0.1, 4)

    def test_mid_conviction(self):
        w = AckmanDruckenmillerEngine._conviction_to_weight(5.0)
        assert w == round(BASE_WEIGHT_PER_SYMBOL * 0.5, 4)

    def test_clamp_above_10(self):
        w = AckmanDruckenmillerEngine._conviction_to_weight(15.0)
        assert w == BASE_WEIGHT_PER_SYMBOL

    def test_clamp_below_1(self):
        w = AckmanDruckenmillerEngine._conviction_to_weight(-5.0)
        assert w == round(BASE_WEIGHT_PER_SYMBOL * 0.1, 4)


# ── evaluate_symbol 테스트 ──────────────────────────────────


class TestEvaluateSymbol:
    def test_buy_expansion(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        d = engine.evaluate_symbol("005930", base_conviction=8.0)
        assert d.action == "buy"
        assert d.regime == "expansion"
        assert d.regime_weight_adjustment == 1.2
        assert d.final_weight > 0

    def test_buy_weight_with_expansion_multiplier(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        d = engine.evaluate_symbol("005930", base_conviction=10.0)
        expected = round(BASE_WEIGHT_PER_SYMBOL * 1.2, 4)
        assert d.final_weight == expected

    def test_skip_no_catalyst(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=0.5, catalyst_count=0),
        )
        d = engine.evaluate_symbol("005930", base_conviction=9.0)
        assert d.action == "skip"
        assert d.final_weight == 0.0

    def test_sell_low_conviction(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
        )
        d = engine.evaluate_symbol("005930", base_conviction=2.0)
        assert d.action == "sell"
        assert d.final_weight == 0.0

    def test_hold_crisis_high_catalyst(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=5.0),
            regime_result=_make_regime_result(regime=Regime.CRISIS),
        )
        d = engine.evaluate_symbol("005930", base_conviction=9.0)
        assert d.action == "hold"
        assert d.final_weight == 0.0

    def test_contraction_multiplier(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.CONTRACTION),
        )
        d = engine.evaluate_symbol("005930", base_conviction=7.0)
        assert d.action == "buy"
        assert d.regime_weight_adjustment == 0.6

    def test_recovery_multiplier(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.RECOVERY),
        )
        d = engine.evaluate_symbol("005930", base_conviction=7.0)
        assert d.action == "buy"
        assert d.regime_weight_adjustment == 1.0

    def test_default_conviction(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=1.5),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        d = engine.evaluate_symbol("005930")
        assert d.conviction == 5.0

    def test_catalyst_score_stored(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=4.2),
        )
        d = engine.evaluate_symbol("005930", base_conviction=7.0)
        assert d.catalyst_score == 4.2


# ── evaluate_portfolio 테스트 ───────────────────────────────


class TestEvaluatePortfolio:
    def test_basic_portfolio(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        p = engine.evaluate_portfolio(
            symbols=["005930", "000660"],
            base_convictions={"005930": 8.0, "000660": 7.0},
        )
        assert p.regime == Regime.EXPANSION
        assert len(p.decisions) == 2
        assert p.total_equity_weight + p.cash_weight == pytest.approx(1.0, abs=0.01)

    def test_cash_floor_expansion(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        p = engine.evaluate_portfolio(
            symbols=["A"],
            base_convictions={"A": 8.0},
        )
        assert p.cash_weight >= REGIME_CASH_FLOOR[Regime.EXPANSION] - 0.01

    def test_cash_floor_crisis(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.CRISIS),
        )
        p = engine.evaluate_portfolio(
            symbols=["A", "B", "C"],
            base_convictions={"A": 9.0, "B": 9.0, "C": 9.0},
        )
        # crisis: all should be hold (no buy in crisis), so equity=0
        assert p.cash_weight >= REGIME_CASH_FLOOR[Regime.CRISIS] - 0.01

    def test_normalization_caps_equity(self):
        """비중 합이 max_equity를 초과하면 정규화"""
        # 많은 종목에 높은 확신 + 확장기 → 정규화 필요
        tracker = MagicMock(spec=CatalystTracker)
        dashboard = MagicMock(spec=MacroRegimeDashboard)

        tracker.score.return_value = _make_catalyst_score(total=5.0)
        dashboard.classify_regime.return_value = _make_regime_result(
            regime=Regime.EXPANSION
        )

        engine = AckmanDruckenmillerEngine(tracker, dashboard)

        symbols = [f"SYM{i:03d}" for i in range(20)]
        convictions = {s: 10.0 for s in symbols}

        p = engine.evaluate_portfolio(symbols, convictions)

        max_equity = 1.0 - REGIME_CASH_FLOOR[Regime.EXPANSION]
        assert p.total_equity_weight <= max_equity + 0.01
        assert p.total_equity_weight + p.cash_weight == pytest.approx(1.0, abs=0.01)

    def test_all_skip_means_full_cash(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=0.0, catalyst_count=0),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        p = engine.evaluate_portfolio(
            symbols=["A", "B"],
            base_convictions={"A": 5.0, "B": 5.0},
        )
        assert p.total_equity_weight == 0.0
        assert p.cash_weight == 1.0

    def test_missing_conviction_defaults_to_5(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=1.5),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        p = engine.evaluate_portfolio(
            symbols=["A"],
            base_convictions={},  # no conviction provided
        )
        assert p.decisions[0].conviction == 5.0

    def test_regime_confidence_propagated(self):
        engine = _make_engine(
            regime_result=_make_regime_result(confidence=0.42),
        )
        p = engine.evaluate_portfolio(
            symbols=["A"],
            base_convictions={"A": 7.0},
        )
        assert p.regime_confidence == 0.42


# ── why_now 테스트 ──────────────────────────────────────────


class TestWhyNow:
    def test_buy_signal(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(
                symbol="005930", total=3.0, top_catalyst="HBM4", urgency="near"
            ),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
        )
        result = engine.why_now("005930")
        assert "005930" in result
        assert "3.00" in result
        assert "HBM4" in result
        assert "near" in result
        assert "expansion" in result
        assert "BUY" in result

    def test_skip_signal(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=0.5, top_catalyst=None, urgency="none"),
        )
        result = engine.why_now("000660")
        assert "SKIP" in result
        assert "none" in result

    def test_no_top_catalyst(self):
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(top_catalyst=None, total=0.0),
        )
        result = engine.why_now("X")
        assert "'none'" in result


# ── 레짐 비중 배수 테스트 ───────────────────────────────────


class TestRegimeWeightMultiplier:
    def test_expansion_multiplier(self):
        assert REGIME_WEIGHT_MULTIPLIER[Regime.EXPANSION] == 1.2

    def test_contraction_multiplier(self):
        assert REGIME_WEIGHT_MULTIPLIER[Regime.CONTRACTION] == 0.6

    def test_crisis_multiplier(self):
        assert REGIME_WEIGHT_MULTIPLIER[Regime.CRISIS] == 0.3

    def test_recovery_multiplier(self):
        assert REGIME_WEIGHT_MULTIPLIER[Regime.RECOVERY] == 1.0

    def test_all_regimes_have_multiplier(self):
        for regime in Regime:
            assert regime in REGIME_WEIGHT_MULTIPLIER


# ── JSON 영속성 테스트 ──────────────────────────────────────


class TestPersistence:
    def test_save_and_load_history(self, tmp_path: Path):
        data_dir = str(tmp_path)
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
            data_dir=data_dir,
        )

        engine.evaluate_portfolio(
            symbols=["A"],
            base_convictions={"A": 8.0},
        )

        history_file = tmp_path / "ackman_druckenmiller_history.json"
        assert history_file.exists()

        content = json.loads(history_file.read_text(encoding="utf-8"))
        assert content["version"] == 1
        assert len(content["history"]) == 1
        assert content["history"][0]["regime"] == "expansion"

    def test_load_existing_history(self, tmp_path: Path):
        history_file = tmp_path / "ackman_druckenmiller_history.json"
        existing = {
            "version": 1,
            "updated_at": "2026-04-01T00:00:00",
            "history": [
                {"regime": "recovery", "decisions": []},
            ],
        }
        history_file.write_text(
            json.dumps(existing, ensure_ascii=False),
            encoding="utf-8",
        )

        engine = _make_engine(data_dir=str(tmp_path))
        assert len(engine._history) == 1

    def test_no_data_dir_no_file(self):
        engine = _make_engine(data_dir=None)
        engine.evaluate_portfolio(
            symbols=["A"],
            base_convictions={"A": 7.0},
        )
        # no error, no file created
        assert len(engine._history) == 1

    def test_multiple_evaluations_append(self, tmp_path: Path):
        data_dir = str(tmp_path)
        engine = _make_engine(
            catalyst_score=_make_catalyst_score(total=3.0),
            regime_result=_make_regime_result(regime=Regime.EXPANSION),
            data_dir=data_dir,
        )

        engine.evaluate_portfolio(["A"], {"A": 8.0})
        engine.evaluate_portfolio(["B"], {"B": 7.0})

        history_file = tmp_path / "ackman_druckenmiller_history.json"
        content = json.loads(history_file.read_text(encoding="utf-8"))
        assert len(content["history"]) == 2
