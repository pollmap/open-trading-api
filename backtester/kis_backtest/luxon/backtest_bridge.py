"""
Luxon Terminal → 백테스트/검증 레이어 연결 어댑터.

OrchestrationReport (분석 결과) → QuantPipeline (리스크 파이프라인)
                                → WalkForwardValidator (OOS 검증)
변환만 담당. 기존 모듈 수정 0줄.

Usage:
    bridge = BacktestBridge()
    pipeline_result = bridge.run_risk_pipeline(report, returns_dict)
    wf_result = bridge.validate_oos(report, returns_dict)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence

from kis_backtest.core.pipeline import PipelineConfig, PipelineResult, QuantPipeline
from kis_backtest.core.walk_forward import WFConfig, WFResult, WalkForwardValidator

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

from .orchestrator import OrchestrationReport

logger = logging.getLogger(__name__)


class BacktestBridge:
    """Luxon 분석 결과 → 백테스트/검증 어댑터.

    executor_bridge.py 와 동일 패턴:
      - OrchestrationReport → QuantPipeline 입력 변환
      - OrchestrationReport → WalkForward 입력 변환
      - 기존 모듈 수정 0줄, 변환 + 위임만
    """

    def __init__(
        self,
        pipeline_config: PipelineConfig | None = None,
        wf_config: WFConfig | None = None,
        mcp_provider: Optional["MCPDataProvider"] = None,
    ) -> None:
        self._pipeline = QuantPipeline(
            config=pipeline_config, mcp_provider=mcp_provider,
        )
        self._wf_config = wf_config or WFConfig()

    # ── 변환 ──────────────────────────────────────────────

    def report_to_pipeline_inputs(
        self,
        report: OrchestrationReport,
    ) -> dict[str, Any]:
        """OrchestrationReport → QuantPipeline.run() 인자 변환.

        매핑:
          decisions[].catalyst_score → factor_scores[].score
          decisions[].final_weight   → optimal_weights[]
          cross_references 에서 섹터 추출 (없으면 "Unknown")
        """
        factor_scores: Dict[str, Dict[str, Any]] = {}
        optimal_weights: Dict[str, float] = {}

        for decision in report.portfolio.decisions:
            # 섹터 추출: cross_references 에서 SECTOR 라벨 검색
            sector = "Unknown"
            refs = report.cross_references.get(decision.symbol, [])
            for ref in refs:
                if ref.startswith("섹터:") or ref.startswith("sector:"):
                    sector = ref.split(":", 1)[1].strip()
                    break

            factor_scores[decision.symbol] = {
                "name": decision.symbol,
                "score": decision.catalyst_score,
                "sector": sector,
            }
            optimal_weights[decision.symbol] = decision.final_weight

        return {
            "factor_scores": factor_scores,
            "optimal_weights": optimal_weights,
        }

    def report_to_weights(
        self,
        report: OrchestrationReport,
    ) -> Dict[str, float]:
        """OrchestrationReport → position_sizes 기반 비중 맵.

        BUY 결정의 position_sizes.weight 만 추출.
        """
        return {ps.symbol: ps.weight for ps in report.position_sizes}

    # ── 리스크 파이프라인 ─────────────────────────────────

    def run_risk_pipeline(
        self,
        report: OrchestrationReport,
        returns_dict: Dict[str, Sequence[float]] | None = None,
        equity_curve: Sequence[float] | None = None,
        backtest_sharpe: float | None = None,
        backtest_max_dd: float | None = None,
    ) -> PipelineResult:
        """OrchestrationReport → QuantPipeline.run() 실행.

        Luxon 분석 결과를 리스크 파이프라인에 통과시켜
        변동성 타겟팅, DD 체크, Kelly 조정을 적용한다.

        Args:
            report: run_workflow 결과.
            returns_dict: 종목별 일간 수익률 (vol 타겟팅용).
            equity_curve: 자산 곡선 (DD 체크용).
            backtest_sharpe: 백테스트 Sharpe (리스크 게이트).
            backtest_max_dd: 백테스트 MaxDD (리스크 게이트).

        Returns:
            PipelineResult: 리스크 조정된 포트폴리오.
        """
        inputs = self.report_to_pipeline_inputs(report)
        return self._pipeline.run(
            factor_scores=inputs["factor_scores"],
            optimal_weights=inputs["optimal_weights"],
            returns_dict=returns_dict,
            equity_curve=equity_curve,
            backtest_sharpe=backtest_sharpe,
            backtest_max_dd=backtest_max_dd,
            strategy_name="Luxon Terminal",
        )

    # ── Walk-Forward OOS 검증 ─────────────────────────────

    def validate_oos(
        self,
        report: OrchestrationReport,
        returns_dict: Dict[str, Sequence[float]],
        n_folds: int | None = None,
        min_sharpe: float | None = None,
    ) -> WFResult:
        """Walk-Forward OOS 검증.

        Luxon position_sizes 비중으로 포트폴리오 수익률을 합성한 뒤
        N-fold IS/OOS 분석을 수행한다.

        Args:
            report: run_workflow 결과.
            returns_dict: 종목별 일간 수익률 (최소 60일).
            n_folds: 폴드 수 (None 이면 config 기본값).
            min_sharpe: 최소 OOS Sharpe (None 이면 config 기본값).

        Returns:
            WFResult: PASS/FAIL + 폴드별 breakdown.
        """
        weights = self.report_to_weights(report)
        if not weights:
            # BUY 결정이 없으면 빈 결과 반환
            config = WFConfig(
                n_folds=n_folds or self._wf_config.n_folds,
                min_sharpe=min_sharpe or self._wf_config.min_sharpe,
            )
            return WFResult(config=config, folds=[], total_days=0)

        config = WFConfig(
            n_folds=n_folds or self._wf_config.n_folds,
            train_ratio=self._wf_config.train_ratio,
            min_sharpe=min_sharpe or self._wf_config.min_sharpe,
            max_oos_dd=self._wf_config.max_oos_dd,
            min_win_rate=self._wf_config.min_win_rate,
            anchored=self._wf_config.anchored,
        )
        validator = WalkForwardValidator(config)
        return validator.validate_multi_asset(returns_dict, weights)

    # ── 피드백 루프 (파이프라인 + 백테스트) ────────────────

    def run_with_feedback(
        self,
        report: OrchestrationReport,
        returns_dict: Dict[str, Sequence[float]] | None = None,
        strategy_id: str = "sma_crossover",
    ) -> PipelineResult:
        """1차 리스크 파이프라인 → MCP 백테스트 → 실제 Sharpe/MDD로 2차.

        MCP 없으면 1차 결과 그대로 반환.
        """
        inputs = self.report_to_pipeline_inputs(report)
        symbols = [d.symbol for d in report.portfolio.decisions if d.action == "buy"]
        return self._pipeline.run_with_backtest_feedback(
            factor_scores=inputs["factor_scores"],
            optimal_weights=inputs["optimal_weights"],
            returns_dict=returns_dict,
            strategy_id=strategy_id,
            symbols=symbols or None,
        )


__all__ = ["BacktestBridge"]
