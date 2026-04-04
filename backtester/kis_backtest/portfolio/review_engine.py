"""주간 복기 엔진

Red Queen's Trap (arXiv:2512.15732): 백테스트 300%+ APY → 실전 70% 자본 감소.
복기 없으면 죽는다.

이 모듈은 포트폴리오 실행 후 성과를 분석하고 피드백을 생성한다.
DBS Bank의 핵심 차별점: 컨트롤 그룹 비교로 ROI 측정.

Flow:
    실행 기록 (trades, equity_curve)
      ↓
    ReviewEngine.weekly_review()
      ↓
    WeeklyReport (성과, 팩터 기여도, 비용 분석, Kill Condition)
      ↓
    Vault 저장 + Discord 공유 + 전략 조정 권고
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence


@dataclass
class TradeRecord:
    """단일 거래 기록"""
    date: str
    ticker: str
    action: str           # BUY / SELL
    quantity: int
    price: float
    amount: float         # 거래 금액
    commission: float     # 실제 수수료
    tax: float            # 실제 세금
    slippage: float = 0.0 # 추정 슬리피지


@dataclass
class KillCondition:
    """CUFA 보고서의 Kill Condition (투자논지 반증 조건)"""
    description: str      # "매출 성장률 10% 미만 2분기 연속"
    metric: str           # "revenue_growth"
    threshold: float      # 0.10
    current_value: Optional[float] = None
    is_triggered: bool = False


@dataclass
class WeeklyReport:
    """주간 복기 리포트"""
    period_start: str
    period_end: str
    generated_at: datetime

    # 성과
    portfolio_return: float       # 기간 수익률
    benchmark_return: float       # KOSPI200 수익률
    excess_return: float          # 초과수익
    cumulative_return: float      # 누적 수익률
    current_equity: float         # 현재 자산

    # 리스크
    max_drawdown: float           # 기간 최대 드로다운
    peak_drawdown: float          # 역대 최대 드로다운
    current_dd_from_peak: float   # 현재 고점 대비
    volatility: float             # 기간 변동성 (연율화)
    sharpe: float                 # 기간 Sharpe

    # 비용
    total_trades: int
    total_commission: float
    total_tax: float
    total_cost: float
    cost_vs_model: float          # 실제/모델 비용 비율

    # 팩터 기여도 (간소화)
    factor_contributions: Dict[str, float]  # {"momentum": 0.02, "value": -0.01, ...}

    # Kill Conditions
    kill_conditions: List[KillCondition]
    any_kill_triggered: bool

    # 권고
    recommendations: List[str]

    def summary(self) -> str:
        lines = [
            f"{'='*60}",
            f"  주간 복기 리포트 ({self.period_start} ~ {self.period_end})",
            f"  생성: {self.generated_at:%Y-%m-%d %H:%M}",
            f"{'='*60}",
            "",
            f"  [성과]",
            f"  포트폴리오: {self.portfolio_return*100:+.2f}%",
            f"  벤치마크:   {self.benchmark_return*100:+.2f}%",
            f"  초과수익:   {self.excess_return*100:+.2f}%",
            f"  누적수익:   {self.cumulative_return*100:+.2f}%",
            f"  현재자산:   {self.current_equity:,.0f}원",
            "",
            f"  [리스크]",
            f"  기간 MaxDD:  {self.max_drawdown*100:.1f}%",
            f"  역대 MaxDD:  {self.peak_drawdown*100:.1f}%",
            f"  현재 DD:     {self.current_dd_from_peak*100:.1f}%",
            f"  변동성(연):  {self.volatility*100:.1f}%",
            f"  Sharpe:      {self.sharpe:.2f}",
            "",
            f"  [비용]",
            f"  거래 횟수:   {self.total_trades}건",
            f"  수수료:      {self.total_commission:,.0f}원",
            f"  세금:        {self.total_tax:,.0f}원",
            f"  총 비용:     {self.total_cost:,.0f}원",
            f"  실제/모델:   {self.cost_vs_model:.2f}x",
        ]

        if self.factor_contributions:
            lines.append("")
            lines.append("  [팩터 기여도]")
            for factor, contrib in sorted(
                self.factor_contributions.items(), key=lambda x: -abs(x[1])
            ):
                lines.append(f"  {factor:>12}: {contrib*100:+.2f}%")

        if self.kill_conditions:
            lines.append("")
            lines.append("  [Kill Conditions]")
            for kc in self.kill_conditions:
                status = "TRIGGERED" if kc.is_triggered else "OK"
                val = f" (현재: {kc.current_value:.2f})" if kc.current_value is not None else ""
                lines.append(f"  [{status:>9}] {kc.description}{val}")

        if self.recommendations:
            lines.append("")
            lines.append("  [권고]")
            for rec in self.recommendations:
                lines.append(f"  → {rec}")

        lines.append(f"\n{'='*60}")
        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Vault 저장용 마크다운"""
        lines = [
            f"# 주간 복기 리포트",
            f"> {self.period_start} ~ {self.period_end} | {self.generated_at:%Y-%m-%d}",
            "",
            "## 성과",
            f"| 지표 | 값 |",
            f"|------|-----|",
            f"| 포트폴리오 | {self.portfolio_return*100:+.2f}% |",
            f"| 벤치마크 (KOSPI200) | {self.benchmark_return*100:+.2f}% |",
            f"| 초과수익 | {self.excess_return*100:+.2f}% |",
            f"| 누적 | {self.cumulative_return*100:+.2f}% |",
            "",
            "## 리스크",
            f"| 지표 | 값 |",
            f"|------|-----|",
            f"| MaxDD (기간) | {self.max_drawdown*100:.1f}% |",
            f"| MaxDD (역대) | {self.peak_drawdown*100:.1f}% |",
            f"| Sharpe | {self.sharpe:.2f} |",
            f"| 변동성 | {self.volatility*100:.1f}% |",
            "",
            "## 비용",
            f"총 {self.total_trades}건, 비용 {self.total_cost:,.0f}원 (모델 대비 {self.cost_vs_model:.2f}x)",
        ]

        if self.kill_conditions:
            lines.extend(["", "## Kill Conditions"])
            for kc in self.kill_conditions:
                emoji = "X" if kc.is_triggered else "O"
                lines.append(f"- [{emoji}] {kc.description}")

        if self.recommendations:
            lines.extend(["", "## 권고"])
            for rec in self.recommendations:
                lines.append(f"- {rec}")

        return "\n".join(lines)


class ReviewEngine:
    """주간 복기 엔진

    Usage:
        engine = ReviewEngine(
            initial_capital=5_000_000,
            benchmark_returns=[0.01, -0.005, ...],  # KOSPI200 일간
            risk_free_rate=0.035,
        )

        report = engine.weekly_review(
            equity_curve=[5_000_000, 5_050_000, ...],
            trades=[TradeRecord(...), ...],
            kill_conditions=[KillCondition(...)],
            model_cost_rate=0.0396,  # 연 3.96% (cost_model 추정)
        )

        print(report.summary())
        vault_md = report.to_markdown()
    """

    def __init__(
        self,
        initial_capital: float = 5_000_000,
        benchmark_returns: Optional[Sequence[float]] = None,
        risk_free_rate: float = 0.035,
    ):
        self.initial_capital = initial_capital
        self.benchmark_returns = list(benchmark_returns or [])
        self.rf = risk_free_rate
        self.all_equity: List[float] = [initial_capital]
        self.all_reports: List[WeeklyReport] = []

    def weekly_review(
        self,
        equity_curve: Sequence[float],
        trades: Optional[List[TradeRecord]] = None,
        kill_conditions: Optional[List[KillCondition]] = None,
        model_cost_rate: float = 0.0396,
        factor_contributions: Optional[Dict[str, float]] = None,
        period_start: str = "",
        period_end: str = "",
    ) -> WeeklyReport:
        """주간 복기 실행"""
        trades = trades or []
        kill_conditions = kill_conditions or []
        factor_contributions = factor_contributions or {}

        eq = list(equity_curve)
        if not eq:
            eq = [self.initial_capital]

        # 자산 곡선 누적
        self.all_equity.extend(eq[1:] if len(eq) > 1 else [])

        # 성과 계산
        period_return = (eq[-1] - eq[0]) / eq[0] if eq[0] > 0 else 0.0
        cum_return = (eq[-1] - self.initial_capital) / self.initial_capital

        # 벤치마크
        bm_return = 0.0
        if self.benchmark_returns:
            n = min(len(eq) - 1, len(self.benchmark_returns))
            if n > 0:
                bm_return = 1.0
                for r in self.benchmark_returns[-n:]:
                    bm_return *= (1 + r)
                bm_return -= 1.0

        # 드로다운
        peak = eq[0]
        max_dd = 0.0
        for v in eq:
            peak = max(peak, v)
            if peak > 0:
                dd = (v - peak) / peak
                max_dd = min(max_dd, dd)

        # 역대 드로다운
        all_peak = self.all_equity[0]
        peak_dd = 0.0
        for v in self.all_equity:
            all_peak = max(all_peak, v)
            if all_peak > 0:
                dd = (v - all_peak) / all_peak
                peak_dd = min(peak_dd, dd)

        current_dd = (eq[-1] - max(self.all_equity)) / max(self.all_equity) if max(self.all_equity) > 0 else 0.0

        # 변동성 + Sharpe
        if len(eq) >= 3:
            daily_rets = [(eq[i] - eq[i-1]) / eq[i-1] for i in range(1, len(eq)) if eq[i-1] > 0]
            if daily_rets:
                mean_r = sum(daily_rets) / len(daily_rets)
                var_r = sum((r - mean_r)**2 for r in daily_rets) / max(len(daily_rets) - 1, 1)
                vol = math.sqrt(var_r) * math.sqrt(252)
                sharpe = (mean_r * 252 - self.rf) / vol if vol > 0 else 0.0
            else:
                vol, sharpe = 0.0, 0.0
        else:
            vol, sharpe = 0.0, 0.0

        # 비용 집계
        total_comm = sum(t.commission for t in trades)
        total_tax = sum(t.tax for t in trades)
        total_cost = total_comm + total_tax + sum(t.slippage for t in trades)

        # 실제/모델 비용 비율
        expected_annual = model_cost_rate * eq[0]
        expected_weekly = expected_annual / 52
        cost_ratio = total_cost / expected_weekly if expected_weekly > 0 else 1.0

        # Kill Condition 체크
        any_kill = any(kc.is_triggered for kc in kill_conditions)

        # 권고 생성
        recs = self._generate_recommendations(
            period_return, bm_return, max_dd, peak_dd, sharpe, cost_ratio, any_kill
        )

        report = WeeklyReport(
            period_start=period_start,
            period_end=period_end,
            generated_at=datetime.now(),
            portfolio_return=period_return,
            benchmark_return=bm_return,
            excess_return=period_return - bm_return,
            cumulative_return=cum_return,
            current_equity=eq[-1],
            max_drawdown=max_dd,
            peak_drawdown=peak_dd,
            current_dd_from_peak=current_dd,
            volatility=vol,
            sharpe=sharpe,
            total_trades=len(trades),
            total_commission=total_comm,
            total_tax=total_tax,
            total_cost=total_cost,
            cost_vs_model=cost_ratio,
            factor_contributions=factor_contributions,
            kill_conditions=kill_conditions,
            any_kill_triggered=any_kill,
            recommendations=recs,
        )

        self.all_reports.append(report)
        return report

    def _generate_recommendations(
        self,
        period_ret: float,
        bm_ret: float,
        max_dd: float,
        peak_dd: float,
        sharpe: float,
        cost_ratio: float,
        any_kill: bool,
    ) -> List[str]:
        recs = []

        if any_kill:
            recs.append("CRITICAL: Kill Condition 발동 — 해당 종목 즉시 재평가 필요")

        if peak_dd < -0.10:
            recs.append(f"WARNING: 역대 MaxDD {peak_dd*100:.1f}% — Millennium 10% 한도 접근")

        if peak_dd < -0.075:
            recs.append("ACTION: 포지션 50% 축소 검토 (7.5% DD 규칙)")

        if cost_ratio > 1.5:
            recs.append(f"비용 경고: 실제 비용이 모델 대비 {cost_ratio:.1f}x — 슬리피지 모델 재보정 필요")

        if period_ret < bm_ret - 0.02:
            recs.append(f"언더퍼폼: 벤치마크 대비 {(period_ret-bm_ret)*100:.1f}%p — 팩터 노출 점검")

        if sharpe < 0.3 and sharpe != 0:
            recs.append(f"Sharpe {sharpe:.2f} — 전략 유효성 재검토")

        if not recs:
            recs.append("정상 운용 중. 다음 리밸런싱까지 유지.")

        return recs
