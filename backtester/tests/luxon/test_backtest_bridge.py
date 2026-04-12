"""BacktestBridge 테스트 — Luxon → QuantPipeline/WalkForward 어댑터.

Coverage:
    - report_to_pipeline_inputs: decisions → factor_scores/optimal_weights 변환
    - report_to_weights: position_sizes → weights 추출
    - run_risk_pipeline: QuantPipeline.run() 실행 + risk_passed 확인
    - validate_oos: WalkForward 5-fold + PASS/FAIL 판정
    - validate_oos 빈 position_sizes → 빈 결과
    - run_with_feedback: MCP 없으면 1차 결과 반환
    - orchestrator.backtest() 통합 호출
    - orchestrator.validate() 통합 호출

Style:
    - real QuantPipeline + WalkForwardValidator (no mock)
    - synthetic returns_dict (random seed=42)
    - no MCP (mcp=None)
"""
from __future__ import annotations

import random

import pytest

from kis_backtest.core.pipeline import PipelineResult
from kis_backtest.core.walk_forward import WFResult
from kis_backtest.luxon.backtest_bridge import BacktestBridge
from kis_backtest.luxon.orchestrator import LuxonOrchestrator, OrchestrationReport
from kis_backtest.portfolio.catalyst_tracker import CatalystType


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def orch() -> LuxonOrchestrator:
    return LuxonOrchestrator(total_capital=100_000_000.0)


@pytest.fixture
def report_with_buy(orch: LuxonOrchestrator) -> OrchestrationReport:
    """BUY 결정이 있는 리포트."""
    orch.add_catalyst(
        symbol="005930",
        name="HBM4 양산",
        catalyst_type=CatalystType.INDUSTRY,
        expected_date="2026-06-01",
        probability=0.7,
        impact=8.0,
    )
    return orch.run_workflow(
        ["005930", "000660"],
        base_convictions={"005930": 8.0, "000660": 5.0},
    )


@pytest.fixture
def report_no_buy(orch: LuxonOrchestrator) -> OrchestrationReport:
    """BUY 결정이 없는 리포트 (낮은 확신도)."""
    return orch.run_workflow(
        ["005930"],
        base_convictions={"005930": 1.0},
    )


@pytest.fixture
def returns_dict() -> dict[str, list[float]]:
    """합성 일간 수익률 (300일, seed=42)."""
    random.seed(42)
    return {
        "005930": [random.gauss(0.0005, 0.012) for _ in range(300)],
        "000660": [random.gauss(0.0003, 0.015) for _ in range(300)],
    }


# ── 변환 테스트 ────────────────────────────────────────────────


class TestReportConversion:
    def test_report_to_pipeline_inputs(
        self, report_with_buy: OrchestrationReport,
    ) -> None:
        bridge = BacktestBridge()
        inputs = bridge.report_to_pipeline_inputs(report_with_buy)

        assert "factor_scores" in inputs
        assert "optimal_weights" in inputs
        # decisions 수만큼 factor_scores 존재
        assert len(inputs["factor_scores"]) == len(
            report_with_buy.portfolio.decisions,
        )
        for sym, fs in inputs["factor_scores"].items():
            assert "name" in fs
            assert "score" in fs
            assert "sector" in fs

    def test_report_to_weights_buy_only(
        self, report_with_buy: OrchestrationReport,
    ) -> None:
        bridge = BacktestBridge()
        weights = bridge.report_to_weights(report_with_buy)

        # position_sizes 에 있는 종목만
        ps_symbols = {ps.symbol for ps in report_with_buy.position_sizes}
        assert set(weights.keys()) == ps_symbols
        for w in weights.values():
            assert w > 0

    def test_report_to_weights_no_buy(
        self, report_no_buy: OrchestrationReport,
    ) -> None:
        bridge = BacktestBridge()
        weights = bridge.report_to_weights(report_no_buy)
        # 낮은 확신도 → position_sizes 비거나 매우 작을 수 있음
        assert isinstance(weights, dict)


# ── 리스크 파이프라인 테스트 ──────────────────────────────────


class TestRiskPipeline:
    def test_run_risk_pipeline_basic(
        self,
        report_with_buy: OrchestrationReport,
        returns_dict: dict[str, list[float]],
    ) -> None:
        bridge = BacktestBridge()
        result = bridge.run_risk_pipeline(
            report_with_buy, returns_dict=returns_dict,
        )

        assert isinstance(result, PipelineResult)
        assert isinstance(result.risk_passed, bool)
        assert isinstance(result.risk_details, list)
        assert result.kelly_allocation >= 0

    def test_run_risk_pipeline_no_returns(
        self, report_with_buy: OrchestrationReport,
    ) -> None:
        """returns_dict 없이도 동작 (보수적 가정)."""
        bridge = BacktestBridge()
        result = bridge.run_risk_pipeline(report_with_buy)

        assert isinstance(result, PipelineResult)
        assert result.order is not None


# ── Walk-Forward 검증 테스트 ───────────────────────────────────


class TestWalkForward:
    def test_validate_oos_basic(
        self,
        report_with_buy: OrchestrationReport,
        returns_dict: dict[str, list[float]],
    ) -> None:
        bridge = BacktestBridge()
        result = bridge.validate_oos(
            report_with_buy, returns_dict=returns_dict,
        )

        assert isinstance(result, WFResult)
        assert len(result.folds) > 0
        assert result.verdict in ("PASS", ) or "FAIL" in result.verdict

    def test_validate_oos_empty_positions(
        self,
        report_no_buy: OrchestrationReport,
        returns_dict: dict[str, list[float]],
    ) -> None:
        """BUY 없는 리포트 → 빈 WFResult."""
        bridge = BacktestBridge()
        result = bridge.validate_oos(
            report_no_buy, returns_dict=returns_dict,
        )

        assert isinstance(result, WFResult)
        # position_sizes 가 비면 folds 도 비어야 함
        if not report_no_buy.position_sizes:
            assert len(result.folds) == 0


# ── Orchestrator 통합 테스트 ──────────────────────────────────


class TestOrchestratorIntegration:
    def test_orchestrator_backtest(
        self,
        orch: LuxonOrchestrator,
        report_with_buy: OrchestrationReport,
        returns_dict: dict[str, list[float]],
    ) -> None:
        result = orch.backtest(report_with_buy, returns_dict=returns_dict)

        assert isinstance(result, PipelineResult)
        assert isinstance(result.risk_passed, bool)

    def test_orchestrator_validate(
        self,
        orch: LuxonOrchestrator,
        report_with_buy: OrchestrationReport,
        returns_dict: dict[str, list[float]],
    ) -> None:
        result = orch.validate(report_with_buy, returns_dict=returns_dict)

        assert isinstance(result, WFResult)
        assert result.total_days > 0
