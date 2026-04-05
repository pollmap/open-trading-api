"""E2E 통합 파이프라인 테스트

전체 파이프라인을 합성 데이터로 검증.
4가지 시나리오: 정상, 섹터집중, DD발생, Kill Condition.
"""

import random
import pytest

from kis_backtest.core.pipeline import QuantPipeline, PipelineConfig
from kis_backtest.portfolio.review_engine import TradeRecord, KillCondition


def _make_returns(n=120, mu=0.0005, sigma=0.015, seed=42):
    random.seed(seed)
    return [random.gauss(mu, sigma) for _ in range(n)]


def _make_equity(start=5_000_000, returns=None, n=20, seed=42):
    if returns is None:
        random.seed(seed)
        returns = [random.gauss(0.001, 0.01) for _ in range(n)]
    eq = [start]
    for r in returns:
        eq.append(eq[-1] * (1 + r))
    return eq


class TestE2EPipelineNormal:
    """시나리오 1: 정상 포트폴리오"""

    def test_full_pipeline_pass(self):
        pipeline = QuantPipeline()

        factor_scores = {
            "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
            "035420": {"name": "NAVER", "score": 0.71, "sector": "플랫폼"},
            "051910": {"name": "LG화학", "score": 0.68, "sector": "화학"},
            "006400": {"name": "삼성SDI", "score": 0.65, "sector": "배터리"},
            "003670": {"name": "포스코퓨처엠", "score": 0.62, "sector": "소재"},
        }
        optimal_weights = {
            "005930": 0.14, "035420": 0.13, "051910": 0.12,
            "006400": 0.11, "003670": 0.10,
        }
        returns_dict = {t: _make_returns(seed=i) for i, t in enumerate(factor_scores)}

        result = pipeline.run(
            factor_scores=factor_scores,
            optimal_weights=optimal_weights,
            returns_dict=returns_dict,
            backtest_sharpe=0.85,
            backtest_max_dd=-0.12,
        )

        assert result.order is not None
        assert result.estimated_annual_cost > 0
        assert result.kelly_allocation >= 0
        # Kelly 적용 후: kelly>0이면 종목 있음, kelly=0이면 전량 현금 (올바른 동작)
        if result.kelly_allocation > 0:
            assert result.order.n_stocks >= 1
        else:
            assert result.order.n_stocks == 0  # 비용 후 알파 부재

    def test_pipeline_then_review(self):
        """파이프라인 실행 후 복기"""
        pipeline = QuantPipeline()

        factor_scores = {
            "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
            "035420": {"name": "NAVER", "score": 0.71, "sector": "플랫폼"},
        }
        result = pipeline.run(
            factor_scores=factor_scores,
            optimal_weights={"005930": 0.15, "035420": 0.12},
            backtest_sharpe=0.8,
            backtest_max_dd=-0.10,
        )
        assert result.order is not None

        # 복기
        equity = _make_equity()
        report = pipeline.review(
            equity_curve=equity,
            period_start="2026-04-01",
            period_end="2026-04-05",
            factor_contributions={"momentum": 0.01, "value": -0.003},
        )
        assert report.portfolio_return != 0
        assert "momentum" in report.factor_contributions


class TestE2EPipelineSectorConcentration:
    """시나리오 2: 섹터 집중 → FAIL → 비중 조정"""

    def test_it_sector_overweight_fails(self):
        pipeline = QuantPipeline()

        factor_scores = {
            "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
            "000660": {"name": "SK하이닉스", "score": 0.75, "sector": "IT"},
            "035420": {"name": "NAVER", "score": 0.71, "sector": "IT"},
        }
        # IT 39% > 35% 한도
        result = pipeline.run(
            factor_scores=factor_scores,
            optimal_weights={"005930": 0.15, "000660": 0.13, "035420": 0.11},
            backtest_sharpe=0.9,
            backtest_max_dd=-0.10,
        )

        assert not result.risk_passed
        assert any("섹터" in d for d in result.risk_details)

    def test_adjusted_weights_pass(self):
        """비중 조정 후 재시도 — IT 비중을 한도 이하로"""
        pipeline = QuantPipeline()

        factor_scores = {
            "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
            "051910": {"name": "LG화학", "score": 0.68, "sector": "화학"},
            "003670": {"name": "포스코퓨처엠", "score": 0.62, "sector": "소재"},
            "035420": {"name": "NAVER", "score": 0.71, "sector": "플랫폼"},
        }
        # IT 15%만 → 35% 한도 이하, 각 종목 15% 이하
        result = pipeline.run(
            factor_scores=factor_scores,
            optimal_weights={"005930": 0.15, "051910": 0.15, "003670": 0.15, "035420": 0.15},
            backtest_sharpe=0.8,
            backtest_max_dd=-0.10,
        )

        assert result.risk_passed


class TestE2EPipelineDrawdown:
    """시나리오 3: DD 발생 → 축소"""

    def test_drawdown_triggers_reduction(self):
        pipeline = QuantPipeline()

        factor_scores = {
            "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
        }

        # -8% DD (> 7.5% reduce 한도)
        equity = [5_000_000, 5_100_000, 5_050_000, 4_800_000, 4_700_000, 4_680_000]

        result = pipeline.run(
            factor_scores=factor_scores,
            optimal_weights={"005930": 0.15},
            equity_curve=equity,
            backtest_sharpe=0.8,
            backtest_max_dd=-0.10,
        )

        assert result.dd_state is not None
        assert "REDUCE" in result.dd_state or "HALT" in result.dd_state

    def test_no_drawdown_normal(self):
        pipeline = QuantPipeline()

        equity = [5_000_000, 5_050_000, 5_100_000]

        result = pipeline.run(
            factor_scores={"005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"}},
            optimal_weights={"005930": 0.10},
            equity_curve=equity,
            backtest_sharpe=0.8,
            backtest_max_dd=-0.10,
        )

        assert result.dd_state == "NORMAL"


class TestE2EPipelineKillCondition:
    """시나리오 4: Kill Condition → 복기 권고"""

    def test_kill_condition_in_review(self):
        pipeline = QuantPipeline()

        equity = _make_equity(n=5)
        kc = KillCondition(
            description="매출 성장률 10% 미만 2분기 연속",
            metric="revenue_growth",
            threshold=0.10,
            current_value=0.05,
            is_triggered=True,
        )

        report = pipeline.review(
            equity_curve=equity,
            kill_conditions=[kc],
            period_start="2026-04-01",
            period_end="2026-04-05",
        )

        assert report.any_kill_triggered
        assert any("Kill Condition" in r for r in report.recommendations)

    def test_no_kill_normal(self):
        pipeline = QuantPipeline()

        equity = _make_equity(n=5)
        kc = KillCondition(
            description="매출 성장률 10% 미만",
            metric="revenue_growth",
            threshold=0.10,
            current_value=0.15,
            is_triggered=False,
        )

        report = pipeline.review(equity_curve=equity, kill_conditions=[kc])
        assert not report.any_kill_triggered


class TestE2EVoltargetIntegration:
    """변동성 타겟팅이 파이프라인에서 실제 작동하는지"""

    def test_portfolio_vol_targeting_scales_total(self):
        """포트폴리오 레벨 vol 타겟팅 — 전체 비중이 스케일됨"""
        pipeline = QuantPipeline(PipelineConfig(target_vol=0.10))

        # 고변동 포트폴리오 (~30% vol)
        high_vol = _make_returns(sigma=0.019, seed=42)
        low_vol = _make_returns(sigma=0.005, seed=43)

        result = pipeline.run(
            factor_scores={
                "HIGH": {"name": "고변동", "score": 0.8, "sector": "A"},
                "LOW": {"name": "저변동", "score": 0.8, "sector": "B"},
            },
            optimal_weights={"HIGH": 0.50, "LOW": 0.50},
            returns_dict={"HIGH": high_vol, "LOW": low_vol},
            backtest_sharpe=0.8,
            backtest_max_dd=-0.10,
        )

        # 포트폴리오 레벨 스케일링이므로 동일한 scale factor
        assert result.vol_adjustments["HIGH"] == result.vol_adjustments["LOW"]
        # 전체 비중이 원래 100%보다 줄어야 함 (고변동 포트폴리오)
        total_alloc = sum(a.target_weight for a in result.order.allocations)
        assert total_alloc < 1.0  # vol 때문에 축소


class TestE2ETurbulence:
    """터뷸런스 감지가 파이프라인에서 작동하는지"""

    def test_extreme_returns_detected(self):
        pipeline = QuantPipeline()

        # 정상 120일 + 마지막 1일 극단
        normal = _make_returns(n=119, sigma=0.01, seed=42)
        extreme_day = 0.08  # 8% 급등
        returns = normal + [extreme_day]

        result = pipeline.run(
            factor_scores={"A": {"name": "A", "score": 0.8, "sector": "X"}},
            optimal_weights={"A": 0.10},
            returns_dict={"A": returns},
            backtest_sharpe=0.8,
            backtest_max_dd=-0.10,
        )

        assert result.turb_index > 1.0  # 평균 이상 스트레스
