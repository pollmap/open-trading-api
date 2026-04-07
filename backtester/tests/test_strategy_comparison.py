"""StrategyComparison 테스트

Phase E: 멀티 전략 백테스트 비교 + BL/HRP 포트폴리오 최적화
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider
from kis_backtest.core.strategy_comparison import (
    ComparisonResult,
    StrategyComparison,
    StrategyResult,
    DEFAULT_STRATEGIES,
    _safe_float,
)


# ============================================================
# StrategyResult 기본
# ============================================================

class TestStrategyResult:
    def test_default_values(self):
        sr = StrategyResult(strategy_id="sma", strategy_name="SMA")
        assert sr.status == "pending"
        assert sr.sharpe is None
        assert sr.total_trades == 0


# ============================================================
# ComparisonResult
# ============================================================

class TestComparisonResult:
    def test_best_by_sharpe(self):
        r = ComparisonResult(
            strategies=[
                StrategyResult("a", "A", sharpe=0.8, status="completed"),
                StrategyResult("b", "B", sharpe=1.2, status="completed"),
                StrategyResult("c", "C", sharpe=None, status="failed"),
            ],
            symbols=["005930"],
            start_date="2021-01-01",
            end_date="2026-01-01",
        )
        assert r.best_by_sharpe.strategy_id == "b"
        assert r.completed_count == 2

    def test_ranking_table(self):
        r = ComparisonResult(
            strategies=[
                StrategyResult("a", "A", sharpe=0.5, annual_return=0.08, max_drawdown=-0.15,
                               total_trades=24, annual_cost=0.004, status="completed"),
                StrategyResult("b", "B", sharpe=1.0, annual_return=0.12, max_drawdown=-0.10,
                               total_trades=48, annual_cost=0.008, status="completed"),
            ],
            symbols=["005930"],
            start_date="2021-01-01",
            end_date="2026-01-01",
        )
        table = r.ranking_table()
        assert table[0]["rank"] == 1
        assert table[0]["strategy"] == "B"  # 더 높은 Sharpe
        assert table[1]["rank"] == 2

    def test_to_dict_serializable(self):
        r = ComparisonResult(
            strategies=[StrategyResult("a", "A", sharpe=0.8, status="completed")],
            symbols=["005930"],
            start_date="2021-01-01",
            end_date="2026-01-01",
            bl_weights={"005930": 0.5},
            hrp_weights={"005930": 0.5},
            recommendation="HRP",
        )
        d = r.to_dict()
        # JSON 직렬화 가능
        json_str = json.dumps(d, ensure_ascii=False)
        assert "HRP" in json_str
        assert "ranking" in d


# ============================================================
# 전략 실행 (mock)
# ============================================================

class TestStrategyExecution:
    @pytest.fixture
    def mock_provider(self):
        provider = MagicMock(spec=MCPDataProvider)

        # 백테스트 결과 mock
        async def _run_bt(**kwargs):
            sid = kwargs.get("strategy_id", "unknown")
            metrics_map = {
                "sma_crossover": {"sharpe_ratio": 0.681, "annual_return": 0.085, "max_drawdown": -0.191},
                "momentum": {"sharpe_ratio": 0.542, "annual_return": 0.065, "max_drawdown": -0.22},
                "volatility_breakout": {"sharpe_ratio": 0.95, "annual_return": 0.11, "max_drawdown": -0.12},
            }
            m = metrics_map.get(sid, {"sharpe_ratio": 0.3})
            return {
                "status": "completed",
                "result": {
                    "metrics": {
                        "risk": {"sharpe_ratio": m["sharpe_ratio"]},
                        "basic": {
                            "annual_return": m.get("annual_return", 0.05),
                            "max_drawdown": m.get("max_drawdown", -0.20),
                            "win_rate": 0.55,
                            "total_trades": 24,
                        },
                    }
                },
            }

        provider.run_and_wait_backtest = AsyncMock(side_effect=_run_bt)
        provider.save_result = MagicMock()
        return provider

    @pytest.mark.asyncio
    async def test_run_single_strategy(self, mock_provider):
        comp = StrategyComparison(mock_provider, symbols=["005930"])
        result = await comp.run_comparison(strategy_ids=["sma_crossover"])

        assert len(result.strategies) == 1
        sr = result.strategies[0]
        assert sr.strategy_id == "sma_crossover"
        assert sr.status == "completed"
        assert sr.sharpe == pytest.approx(0.681)

    @pytest.mark.asyncio
    async def test_run_comparison_3strategies(self, mock_provider):
        comp = StrategyComparison(mock_provider, symbols=["005930", "000660"])
        result = await comp.run_comparison()  # DEFAULT_STRATEGIES

        assert len(result.strategies) == 3
        assert result.completed_count == 3

        # vol_breakout이 Sharpe 최고
        best = result.best_by_sharpe
        assert best.strategy_id == "volatility_breakout"
        assert best.sharpe == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_strategy_failure_handled(self, mock_provider):
        """백테스트 실패 시 graceful 처리"""
        mock_provider.run_and_wait_backtest = AsyncMock(
            side_effect=RuntimeError("MCP timeout")
        )
        comp = StrategyComparison(mock_provider, symbols=["005930"])
        result = await comp.run_comparison(strategy_ids=["sma_crossover"])

        assert result.strategies[0].status == "failed"
        assert result.completed_count == 0


# ============================================================
# BL/HRP 포트폴리오 최적화
# ============================================================

class TestPortfolioOptimization:
    @pytest.fixture
    def mock_provider(self):
        provider = MagicMock(spec=MCPDataProvider)

        async def _bl(prices_dict, **kw):
            return {"005930": 0.6, "000660": 0.4}

        async def _hrp(prices_dict):
            return {"005930": 0.5, "000660": 0.5}

        provider.get_bl_weights = AsyncMock(side_effect=_bl)
        provider.get_hrp_weights = AsyncMock(side_effect=_hrp)
        return provider

    @pytest.mark.asyncio
    async def test_optimize_bl_hrp(self, mock_provider):
        """BL/HRP에 가격 시계열(dict)이 전달되는지 확인"""
        comp = StrategyComparison(mock_provider, symbols=["005930", "000660"])
        # 가격 시계열 (float 배열이 아님)
        prices_dict = {
            "005930": [{"date": f"2025-01-{i+1:02d}", "close": 70000 + i} for i in range(60)],
            "000660": [{"date": f"2025-01-{i+1:02d}", "close": 130000 + i} for i in range(60)],
        }
        factor_scores = {
            "005930": {"name": "삼성전자", "score": 0.8, "sector": "IT"},
            "000660": {"name": "SK하이닉스", "score": 0.7, "sector": "IT"},
        }

        bl, hrp = await comp.optimize_portfolio(prices_dict, factor_scores)

        assert bl["005930"] == pytest.approx(0.6)
        assert hrp["005930"] == pytest.approx(0.5)

    def test_select_weights_hrp_preferred(self):
        """HRP가 더 균등하면 HRP 선택"""
        comp = StrategyComparison(MagicMock(), symbols=[])
        bl = {"A": 0.8, "B": 0.2}  # 불균등
        hrp = {"A": 0.5, "B": 0.5}  # 균등

        selected, reason = comp.select_weights(bl, hrp)
        assert selected == hrp
        assert "HRP" in reason

    def test_select_weights_bl_when_hrp_fails(self):
        comp = StrategyComparison(MagicMock(), symbols=[])
        selected, reason = comp.select_weights({"A": 0.6}, {})
        assert selected == {"A": 0.6}
        assert "BL" in reason

    def test_select_weights_both_empty(self):
        comp = StrategyComparison(MagicMock(), symbols=[])
        selected, reason = comp.select_weights({}, {})
        assert selected == {}
        assert "EQUAL" in reason


# ============================================================
# 유틸리티
# ============================================================

class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) is None

    def test_string_number(self):
        assert _safe_float("0.681") == pytest.approx(0.681)

    def test_invalid_string(self):
        assert _safe_float("N/A") is None

    def test_int(self):
        assert _safe_float(42) == pytest.approx(42.0)
