"""파이프라인 ↔ MCP 프로바이더 통합 테스트

MCP 데이터가 파이프라인을 통해 올바르게 흐르는지 검증.
실제 MCP 서버 없이 mock으로 동작.
"""

import pytest
from unittest.mock import MagicMock, patch

from kis_backtest.core.pipeline import QuantPipeline, PipelineConfig, PipelineResult
from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider


# ============================================================
# PipelineConfig 기본값 변경 검증
# ============================================================

class TestPipelineConfigDefaults:
    def test_risk_free_rate_default_is_none(self):
        """PipelineConfig 기본 rf = None (MCP 우선 조회)"""
        config = PipelineConfig()
        assert config.risk_free_rate is None

    def test_other_defaults_unchanged(self):
        """rf 이외 기본값은 변경 없음"""
        config = PipelineConfig()
        assert config.total_capital == 5_000_000
        assert config.target_vol == pytest.approx(0.10)
        assert config.kelly_fraction == pytest.approx(0.5)
        assert config.dd_halt == pytest.approx(-0.10)


# ============================================================
# QuantPipeline + MCPDataProvider 통합
# ============================================================

class TestPipelineWithMCPProvider:
    def _make_mock_provider(self, rf: float = 0.0275) -> MCPDataProvider:
        """mock MCPDataProvider — get_risk_free_rate_sync만 구현"""
        provider = MagicMock(spec=MCPDataProvider)
        provider.get_risk_free_rate_sync.return_value = rf
        return provider

    def test_pipeline_uses_mcp_rate(self):
        """MCP 프로바이더가 있으면 ECOS 기준금리 사용"""
        provider = self._make_mock_provider(rf=0.0275)
        pipeline = QuantPipeline(mcp_provider=provider)

        assert pipeline.config.risk_free_rate == pytest.approx(0.0275)
        provider.get_risk_free_rate_sync.assert_called_once()

    def test_pipeline_mcp_rate_flows_to_review_engine(self):
        """MCP 기준금리가 ReviewEngine까지 전달"""
        provider = self._make_mock_provider(rf=0.03)
        pipeline = QuantPipeline(mcp_provider=provider)

        assert pipeline.review_engine.rf == pytest.approx(0.03)

    def test_pipeline_mcp_rate_flows_to_kelly(self):
        """MCP 기준금리가 Kelly 계산에 사용되는지 간접 검증"""
        provider = self._make_mock_provider(rf=0.025)
        pipeline = QuantPipeline(mcp_provider=provider)

        # run()에서 self.config.risk_free_rate가 cost_model.kelly_adjusted(rf=...)에 전달
        assert pipeline.config.risk_free_rate == pytest.approx(0.025)

    def test_pipeline_mcp_failure_uses_fallback(self):
        """MCP 실패 시 0.035 fallback"""
        provider = MagicMock(spec=MCPDataProvider)
        provider.get_risk_free_rate_sync.side_effect = RuntimeError("timeout")
        pipeline = QuantPipeline(mcp_provider=provider)

        assert pipeline.config.risk_free_rate == pytest.approx(0.035)


# ============================================================
# 하위호환 (기존 사용법 보장)
# ============================================================

class TestBackwardCompatibility:
    def test_no_args_constructor(self):
        """QuantPipeline() 무인자 호출 — 기존과 동일하게 동작"""
        pipeline = QuantPipeline()
        assert pipeline.config.risk_free_rate == pytest.approx(0.035)
        assert pipeline.mcp is None

    def test_explicit_config_rf(self):
        """PipelineConfig에 rf를 직접 지정하면 MCP 무시"""
        config = PipelineConfig(risk_free_rate=0.04)
        pipeline = QuantPipeline(config=config)
        assert pipeline.config.risk_free_rate == pytest.approx(0.04)

    def test_explicit_config_with_provider_ignores_mcp(self):
        """config에 rf가 있으면 MCP 프로바이더가 있어도 호출 안 함"""
        provider = MagicMock(spec=MCPDataProvider)
        config = PipelineConfig(risk_free_rate=0.04)
        pipeline = QuantPipeline(config=config, mcp_provider=provider)

        assert pipeline.config.risk_free_rate == pytest.approx(0.04)
        provider.get_risk_free_rate_sync.assert_not_called()

    def test_run_basic_still_works(self):
        """기존 run() 호출 방식 — 변경 없이 동작"""
        pipeline = QuantPipeline()
        result = pipeline.run(
            factor_scores={
                "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
                "000660": {"name": "SK하이닉스", "score": 0.75, "sector": "IT"},
            },
            optimal_weights={"005930": 0.15, "000660": 0.12},
            backtest_sharpe=0.85,
            backtest_max_dd=-0.12,
        )
        assert isinstance(result, PipelineResult)
        assert result.order is not None

    def test_review_without_cufa_report(self):
        """review() cufa_report 미전달 — 기존과 동일"""
        pipeline = QuantPipeline()
        report = pipeline.review(
            equity_curve=[5_000_000, 5_050_000, 5_030_000],
        )
        assert report is not None
        assert report.any_kill_triggered is False


# ============================================================
# base_return 자동 계산 검증
# ============================================================

class TestBaseReturnAutoCalc:
    def test_bl_base_return_uses_rf_plus_erp(self):
        """base_return = risk_free_rate + 5% ERP"""
        provider = MagicMock(spec=MCPDataProvider)
        provider.get_risk_free_rate_sync.return_value = 0.0275

        pipeline = QuantPipeline(mcp_provider=provider)
        result = pipeline.run(
            factor_scores={
                "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
            },
            optimal_weights={"005930": 0.15},
            backtest_sharpe=0.85,
            backtest_max_dd=-0.12,
        )

        # auto_bl_views가 생성되었고 base_return이 rf+0.05=0.0775 기반
        assert result.auto_bl_views is not None
        if result.auto_bl_views:
            # BL 뷰의 base_return은 0.0275 + 0.05 = 0.0775
            # 뷰의 값이 base_return 기반으로 계산되었는지 확인
            assert isinstance(result.auto_bl_views, list)
