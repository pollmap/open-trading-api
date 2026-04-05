"""퀀트 운용 통합 파이프라인

모든 부품(리스크 모듈, 브릿지, 복기 엔진)을 하나의 E2E 파이프라인으로 엮는다.

Flow:
    종목+팩터점수+비중 (MCP 결과 또는 수동)
      → 변동성 타겟팅
      → 거래비용 계산
      → 리스크 게이트 체크
      → PortfolioOrder 생성
      → (선택) 백테스트 실행
      → (선택) 복기 리포트 생성
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

logger = logging.getLogger(__name__)

from kis_backtest.strategies.risk.cost_model import (
    KoreaTransactionCostModel,
    Market,
)
from kis_backtest.strategies.risk.drawdown_guard import (
    DrawdownGuard,
    check_concentration,
    ConcentrationLimits,
)
from kis_backtest.strategies.risk.vol_target import (
    VolatilityTargeter,
    turbulence_index,
)
from kis_backtest.strategies.risk.correlation_monitor import (
    CorrelationMonitor,
)
from kis_backtest.portfolio.mcp_bridge import (
    MCPBridge,
    PortfolioOrder,
    OrderAction,
)
from kis_backtest.portfolio.review_engine import (
    ReviewEngine,
    WeeklyReport,
    TradeRecord,
    KillCondition,
)
from kis_backtest.portfolio.factor_to_views import (
    factor_scores_to_bl_views,
    bl_views_to_mcp_format,
    views_summary,
)


@dataclass
class PipelineConfig:
    """파이프라인 설정"""
    total_capital: float = 5_000_000
    target_vol: float = 0.10
    max_leverage: float = 1.5
    kelly_fraction: float = 0.5
    rebalance_frequency: str = "monthly"
    dd_warning: float = -0.05
    dd_reduce: float = -0.075
    dd_halt: float = -0.10
    max_single_stock: float = 0.15
    max_single_sector: float = 0.35
    min_sharpe: float = 0.5
    max_drawdown: float = -0.20
    slippage_bps: float = 5.0
    risk_free_rate: Optional[float] = None  # None → MCP에서 런타임 조회, fallback 0.035


@dataclass
class PipelineResult:
    """파이프라인 실행 결과"""
    order: Optional[PortfolioOrder]
    risk_passed: bool
    risk_details: List[str]
    vol_adjustments: Dict[str, float]  # ticker → scale_factor
    turb_index: float
    dd_state: Optional[str]
    estimated_annual_cost: float
    kelly_allocation: float
    auto_bl_views: Optional[List] = None  # 팩터→BL 자동 뷰 (None이면 수동 비중 사용)


class QuantPipeline:
    """퀀트 운용 통합 파이프라인

    Usage:
        pipeline = QuantPipeline()

        result = pipeline.run(
            factor_scores={
                "005930": {"name": "삼성전자", "score": 0.82, "sector": "IT"},
                "000660": {"name": "SK하이닉스", "score": 0.75, "sector": "IT"},
            },
            optimal_weights={"005930": 0.15, "000660": 0.12},
            returns_dict={
                "005930": [0.01, -0.005, 0.008, ...],
                "000660": [0.015, -0.01, 0.003, ...],
            },
        )

        if result.risk_passed:
            print(result.order.summary())
        else:
            print("리스크 게이트 FAIL:", result.risk_details)
    """

    def __init__(
        self,
        config: Optional[PipelineConfig] = None,
        mcp_provider: Optional["MCPDataProvider"] = None,
    ):
        self.config = config or PipelineConfig()
        self.mcp = mcp_provider

        # risk_free_rate 해결: MCP → config → fallback
        if self.config.risk_free_rate is None:
            if self.mcp:
                try:
                    self.config.risk_free_rate = self.mcp.get_risk_free_rate_sync()
                    logger.info("MCP 기준금리 적용: %.4f", self.config.risk_free_rate)
                except Exception as e:
                    logger.warning("MCP 기준금리 조회 실패, fallback 사용: %s", e)
                    self.config.risk_free_rate = 0.035
            else:
                self.config.risk_free_rate = 0.035

        self.cost_model = KoreaTransactionCostModel(
            slippage_bps=self.config.slippage_bps,
        )
        self.vol_targeter = VolatilityTargeter(
            target_vol=self.config.target_vol,
            max_leverage=self.config.max_leverage,
        )
        self.dd_guard = DrawdownGuard(
            warning_pct=self.config.dd_warning,
            reduce_pct=self.config.dd_reduce,
            halt_pct=self.config.dd_halt,
        )
        self.bridge = MCPBridge(
            total_capital=self.config.total_capital,
            cost_model=self.cost_model,
            vol_targeter=self.vol_targeter,
            concentration_limits=ConcentrationLimits(
                max_single_stock=self.config.max_single_stock,
                max_single_sector=self.config.max_single_sector,
            ),
            kelly_fraction=self.config.kelly_fraction,
            min_sharpe=self.config.min_sharpe,
            max_drawdown=self.config.max_drawdown,
        )
        self.corr_monitor = CorrelationMonitor(
            threshold=self.config.max_single_sector,  # 0.35 → 상관 한도도 유사
            lookback=60,
        )
        self.review_engine = ReviewEngine(
            initial_capital=self.config.total_capital,
            risk_free_rate=self.config.risk_free_rate,
        )

    def run(
        self,
        factor_scores: Dict[str, Dict],
        optimal_weights: Dict[str, float],
        returns_dict: Optional[Dict[str, Sequence[float]]] = None,
        current_weights: Optional[Dict[str, float]] = None,
        equity_curve: Optional[Sequence[float]] = None,
        backtest_sharpe: Optional[float] = None,
        backtest_max_dd: Optional[float] = None,
        strategy_name: str = "korean-multifactor",
    ) -> PipelineResult:
        """전체 파이프라인 실행

        Args:
            factor_scores: MCP factor_score 결과 {ticker: {name, score, sector}}
            optimal_weights: MCP BL/HRP 결과 {ticker: weight}
            returns_dict: 종목별 일간 수익률 (변동성 타겟팅용)
            current_weights: 현재 보유 비중 (리밸런싱용)
            equity_curve: 현재까지 자산 곡선 (DD 체크용)
            backtest_sharpe: 백테스트 Sharpe (리스크 게이트)
            backtest_max_dd: 백테스트 MaxDD (리스크 게이트)
        """
        returns_dict = returns_dict or {}
        risk_details = []

        # 0. 공매도 불가 — 음수 비중 자동 클리핑 (개인투자자 제약)
        clipped_tickers = [t for t, w in optimal_weights.items() if w < 0]
        clipped_weights = {t: max(0.0, w) for t, w in optimal_weights.items()}

        # 음수가 있었을 때만 재정규화 (없으면 원래 비중 유지 → 현금 비중 보존)
        if clipped_tickers:
            clip_total = sum(clipped_weights.values())
            if clip_total > 0:
                clipped_weights = {t: w / clip_total for t, w in clipped_weights.items()}
            names = [factor_scores.get(t, {}).get("name", t) for t in clipped_tickers]
            risk_details.append(f"공매도 불가: {', '.join(names)} 숏 신호 → 0% 클리핑 + 재정규화")

        # 1. 포트폴리오 레벨 변동성 타겟팅
        #    종목별이 아닌 포트폴리오 전체 vol을 target에 맞춤
        #    이전: 종목별 scale → 현금 86% (비현실적)
        #    현재: 포트폴리오 vol 추정 → 전체 스케일링 (현실적 비중)
        vol_adjustments = {}
        adjusted_weights = dict(clipped_weights)

        if returns_dict:
            import math
            # 포트폴리오 일간 수익률 추정 (가중합)
            min_len = min(
                (len(returns_dict[t]) for t in clipped_weights if returns_dict.get(t)),
                default=0,
            )
            if min_len > 30:
                port_daily = []
                for i in range(min_len):
                    day_ret = sum(
                        clipped_weights.get(t, 0) * returns_dict[t][i]
                        for t in clipped_weights
                        if t in returns_dict and i < len(returns_dict[t])
                    )
                    port_daily.append(day_ret)

                # EWMA 포트폴리오 변동성
                port_vol_est = self.vol_targeter.estimate_vol(port_daily)

                if port_vol_est > 0:
                    portfolio_scale = min(
                        self.config.target_vol / port_vol_est,
                        self.config.max_leverage,
                    )
                    for ticker in adjusted_weights:
                        adjusted_weights[ticker] *= portfolio_scale
                        vol_adjustments[ticker] = portfolio_scale
                else:
                    for ticker in adjusted_weights:
                        vol_adjustments[ticker] = 1.0
            else:
                for ticker in adjusted_weights:
                    vol_adjustments[ticker] = 1.0
        else:
            for ticker in adjusted_weights:
                vol_adjustments[ticker] = 1.0

        # 비중 상한 (max_leverage 초과 방지)
        total_w = sum(adjusted_weights.values())
        if total_w > self.config.max_leverage:
            for ticker in adjusted_weights:
                adjusted_weights[ticker] *= self.config.max_leverage / total_w

        # 2. 터뷸런스 인덱스
        turb = 0.0
        if returns_dict:
            tickers = list(returns_dict.keys())
            if tickers and len(returns_dict[tickers[0]]) > 10:
                current_rets = [returns_dict[t][-1] if returns_dict.get(t) else 0 for t in tickers]
                hist_rets = []
                min_len = min(len(returns_dict[t]) for t in tickers if returns_dict.get(t))
                for i in range(max(0, min_len - 50), min_len - 1):
                    hist_rets.append([returns_dict[t][i] if i < len(returns_dict.get(t, [])) else 0 for t in tickers])
                if hist_rets:
                    turb = turbulence_index(current_rets, hist_rets)

        if turb > 5.0:
            risk_details.append(f"TURB WARNING: 터뷸런스 {turb:.1f}x (>5x, 위기 수준)")

        # 2a-1. VPIN 마이크로구조 체크 (MCP 있을 때만, 비중 실제 조정)
        if self.mcp and factor_scores:
            for ticker in list(factor_scores.keys())[:10]:
                try:
                    toxicity = self.mcp.get_micro_toxicity_sync(ticker)
                    vpin = toxicity.get("vpin", 0) if toxicity else 0
                    name = factor_scores.get(ticker, {}).get("name", ticker)
                    if vpin > 0.7:
                        adjusted_weights[ticker] = adjusted_weights.get(ticker, 0) * 0.5
                        risk_details.append(
                            f"VPIN CRITICAL: {name} 비중 50% 감소 (VPIN={vpin:.2f})"
                        )
                    elif vpin > 0.5:
                        adjusted_weights[ticker] = adjusted_weights.get(ticker, 0) * 0.8
                        risk_details.append(
                            f"VPIN WARNING: {name} 비중 20% 감소 (VPIN={vpin:.2f})"
                        )
                except Exception:
                    pass  # MCP 실패 시 무시

        # 2b. 상관관계 모니터
        if returns_dict and len([t for t in returns_dict if len(returns_dict[t]) >= 30]) >= 2:
            corr_alert = self.corr_monitor.check(returns_dict)
            if corr_alert.is_elevated:
                risk_details.append(f"CORR: {corr_alert.message}")

        # 3. 드로다운 체크
        dd_state = None
        if equity_curve and len(equity_curve) > 1:
            peak = max(equity_curve)
            state = self.dd_guard.check(equity_curve[-1], peak)
            dd_state = state.action
            if state.is_breached:
                risk_details.append(f"DD BREACH: {state.action}")
                # DD 발생 시 전 비중 축소
                for ticker in adjusted_weights:
                    adjusted_weights[ticker] *= state.reduction_factor

        # 3b. 알파 혼잡도 체크 (MCP 있을 때만, 비중 실제 조정)
        if self.mcp and factor_scores:
            try:
                crowding = self.mcp.get_alpha_crowding_sync(list(factor_scores.keys()))
                for ticker, pct in crowding.items():
                    if pct > 0.8:
                        name = factor_scores.get(ticker, {}).get("name", ticker)
                        adjusted_weights[ticker] = adjusted_weights.get(ticker, 0) * 0.7
                        risk_details.append(
                            f"CROWDING: {name} 비중 30% 감소 (혼잡도 {pct:.0%})"
                        )
            except Exception:
                pass  # MCP 실패 시 무시

        # 4. After-cost Kelly 체크 — 실제 데이터에서 mu/sigma 계산 (가정값 금지)
        freq_to_rt = {"weekly": 50, "biweekly": 24, "monthly": 12, "quarterly": 4}
        n_rt = freq_to_rt.get(self.config.rebalance_frequency, 12)

        # 포트폴리오 가중평균 수익률/변동성 계산
        port_mu = 0.0
        port_var = 0.0
        if returns_dict and adjusted_weights:
            import math
            all_rets = []
            for ticker, weight in adjusted_weights.items():
                rets = returns_dict.get(ticker, [])
                if rets and weight > 0:
                    mean_r = sum(rets) / len(rets) * 252  # 연율화
                    var_r = sum((r - sum(rets)/len(rets))**2 for r in rets) / max(len(rets)-1, 1) * 252
                    port_mu += weight * mean_r
                    port_var += (weight ** 2) * var_r  # 단순 가정 (무상관)
            port_sigma = math.sqrt(port_var) if port_var > 0 else 0.20  # 계산 불가 시 보수적 20%
        else:
            # 데이터 없으면 backtest_sharpe에서 역산, 그것도 없으면 보수적 가정
            port_sigma = 0.20
            if backtest_sharpe is not None and backtest_sharpe > 0:
                port_mu = backtest_sharpe * port_sigma + self.config.risk_free_rate
            else:
                port_mu = 0.10  # 최소 보수적 가정 (데이터 부재 시)

        kelly_alloc = self.cost_model.kelly_adjusted(
            mu=port_mu, sigma=port_sigma, rf=self.config.risk_free_rate,
            n_roundtrips=n_rt, fraction=self.config.kelly_fraction,
        )

        # 4b. Kelly를 비중에 실제 적용 (장식→실전)
        if 0 < kelly_alloc < 1.0:
            for ticker in adjusted_weights:
                adjusted_weights[ticker] *= kelly_alloc
            risk_details.append(
                f"KELLY: {kelly_alloc:.2f}x 적용 (Half-Kelly, after-cost)"
            )
        elif kelly_alloc <= 0:
            for ticker in adjusted_weights:
                adjusted_weights[ticker] = 0.0
            risk_details.append("KELLY ZERO: 비용 후 알파 부재 → 전량 현금")

        # 5. 브릿지 → PortfolioOrder
        order = self.bridge.build_order(
            strategy_name=strategy_name,
            factor_scores=factor_scores,
            optimal_weights=adjusted_weights,
            rebalance_frequency=self.config.rebalance_frequency,
            current_weights=current_weights,
            backtest_sharpe=backtest_sharpe,
            backtest_max_dd=backtest_max_dd,
        )

        # 6. 팩터→BL 자동 뷰 생성 (참고용 — 다음 리밸런싱에서 MCP BL 호출 시 사용)
        #    base_return = risk_free_rate + 한국 주식 ERP(약 5%)
        bl_base_return = self.config.risk_free_rate + 0.05
        auto_views = factor_scores_to_bl_views(
            factor_scores,
            base_return=bl_base_return,
            spread=0.10,
            long_only=True,
        )

        # 종합 리스크 판정
        all_details = risk_details + [d for d in order.risk_gate_details if d != "ALL PASS"]
        risk_passed = order.risk_gate_passed and not any("BREACH" in d for d in risk_details)

        # 다중 VPIN CRITICAL → 전체 차단
        vpin_criticals = [d for d in risk_details if "VPIN CRITICAL" in d]
        if len(vpin_criticals) >= 3:
            risk_passed = False
            all_details.append("RISK HALT: 3+ 종목 VPIN CRITICAL → 전체 주문 차단")

        return PipelineResult(
            order=order,
            risk_passed=risk_passed,
            risk_details=all_details if all_details else ["ALL PASS"],
            vol_adjustments=vol_adjustments,
            turb_index=turb,
            dd_state=dd_state,
            estimated_annual_cost=self.cost_model.annual_cost(n_rt),
            kelly_allocation=kelly_alloc,
            auto_bl_views=auto_views,
        )

    def review(
        self,
        equity_curve: Sequence[float],
        trades: Optional[List[TradeRecord]] = None,
        kill_conditions: Optional[List[KillCondition]] = None,
        factor_contributions: Optional[Dict[str, float]] = None,
        period_start: str = "",
        period_end: str = "",
        cufa_report: Optional[Dict] = None,
    ) -> WeeklyReport:
        """복기 실행 (파이프라인에서 직접 호출)

        Args:
            cufa_report: CUFA 보고서 dict → Kill Conditions 자동 추출 (Phase 3 CUFABridge)
        """
        # CUFA 보고서가 있고 kill_conditions가 미지정이면 자동 추출
        if cufa_report and not kill_conditions:
            try:
                from kis_backtest.portfolio.cufa_bridge import CUFABridge
                kill_conditions = CUFABridge.parse_kill_conditions(cufa_report)
                if self.mcp:
                    kill_conditions = CUFABridge.evaluate_kill_conditions(
                        kill_conditions, self.mcp
                    )
            except ImportError:
                pass  # cufa_bridge 미설치 시 무시
        freq_to_rt = {"weekly": 50, "biweekly": 24, "monthly": 12, "quarterly": 4}
        n_rt = freq_to_rt.get(self.config.rebalance_frequency, 12)

        return self.review_engine.weekly_review(
            equity_curve=equity_curve,
            trades=trades,
            kill_conditions=kill_conditions,
            model_cost_rate=self.cost_model.annual_cost(n_rt),
            factor_contributions=factor_contributions,
            period_start=period_start,
            period_end=period_end,
        )

    def run_with_backtest_feedback(
        self,
        factor_scores: Dict[str, Dict],
        optimal_weights: Dict[str, float],
        returns_dict: Optional[Dict[str, Sequence[float]]] = None,
        strategy_id: str = "sma_crossover",
        symbols: Optional[List[str]] = None,
        **kwargs,
    ) -> PipelineResult:
        """1차 실행 → 백테스트 → 실제 Sharpe/MDD로 2차 실행 (피드백 루프)"""
        # 1차: 가정값으로 실행
        result1 = self.run(factor_scores, optimal_weights, returns_dict, **kwargs)

        if not self.mcp:
            return result1

        # 백테스트 실행
        bt_symbols = symbols or list(factor_scores.keys())[:5]
        try:
            bt = self.mcp.run_and_wait_backtest_sync(
                strategy_id=strategy_id, symbols=bt_symbols,
            )
        except Exception as e:
            logger.warning("백테스트 피드백 실패: %s", e)
            return result1

        if bt.get("status") != "completed":
            return result1

        # 2차: 실제 백테스트 Sharpe/MDD로 재실행
        metrics = bt.get("result", {}).get("metrics", {})
        real_sharpe = metrics.get("risk", {}).get("sharpe_ratio")
        real_mdd = metrics.get("basic", {}).get("max_drawdown")

        result2 = self.run(
            factor_scores, optimal_weights, returns_dict,
            backtest_sharpe=real_sharpe,
            backtest_max_dd=-abs(float(real_mdd)) if real_mdd else None,
            **kwargs,
        )
        feedback_msg = f"FEEDBACK: 백테스트 Sharpe={real_sharpe}, MDD={real_mdd} 반영"
        result2.risk_details.insert(0, feedback_msg)
        return result2
