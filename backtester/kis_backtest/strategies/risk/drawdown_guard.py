"""드로다운 보호 모듈

포트폴리오 최대 낙폭 한도를 초과하면 포지션을 자동 축소.
"살아남는 것이 수익보다 중요하다" — Ed Thorp

References:
    - Thorp (2006), "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market"
    - 2×Kelly 초과 시 복리 수익 = 0, 파산 경로 진입
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class DrawdownState:
    """드로다운 상태"""
    current_value: float          # 현재 포트폴리오 가치
    peak_value: float             # 고점
    drawdown_pct: float           # 현재 드로다운 (음수)
    is_breached: bool             # 한도 초과 여부
    action: str                   # 권장 행동
    reduction_factor: float       # 포지션 축소 비율 (1.0 = 변경 없음)


class DrawdownGuard:
    """포트폴리오 드로다운 보호

    3단계 경보 시스템:
    - WARNING: -10% → 신규 매수 중단
    - REDUCE:  -15% → 전 포지션 50% 축소
    - HALT:    -20% → 전 포지션 청산, 찬희 승인 필요

    Usage:
        guard = DrawdownGuard(
            warning_pct=-0.10,
            reduce_pct=-0.15,
            halt_pct=-0.20,
            reduce_factor=0.5,
        )

        state = guard.check(current_value=9_200_000, peak_value=10_000_000)
        print(state.action)  # "REDUCE: 50% 축소"
        print(state.reduction_factor)  # 0.5
    """

    def __init__(
        self,
        warning_pct: float = -0.10,
        reduce_pct: float = -0.15,
        halt_pct: float = -0.20,
        reduce_factor: float = 0.5,
    ):
        if not (halt_pct < reduce_pct < warning_pct < 0):
            raise ValueError(
                f"한도 순서 오류: halt({halt_pct}) < reduce({reduce_pct}) < warning({warning_pct}) < 0"
            )

        self.warning_pct = warning_pct
        self.reduce_pct = reduce_pct
        self.halt_pct = halt_pct
        self.reduce_factor = reduce_factor

    def check(
        self,
        current_value: float,
        peak_value: float,
    ) -> DrawdownState:
        """현재 드로다운 상태 확인

        Args:
            current_value: 현재 포트폴리오 가치
            peak_value: 역대 고점 가치
        """
        if peak_value <= 0:
            return DrawdownState(
                current_value=current_value,
                peak_value=peak_value,
                drawdown_pct=0.0,
                is_breached=False,
                action="NORMAL",
                reduction_factor=1.0,
            )

        dd = (current_value - peak_value) / peak_value

        if dd <= self.halt_pct:
            return DrawdownState(
                current_value=current_value,
                peak_value=peak_value,
                drawdown_pct=dd,
                is_breached=True,
                action=f"HALT: 전 포지션 청산 ({dd*100:.1f}% < {self.halt_pct*100:.0f}% 한도)",
                reduction_factor=0.0,
            )
        elif dd <= self.reduce_pct:
            return DrawdownState(
                current_value=current_value,
                peak_value=peak_value,
                drawdown_pct=dd,
                is_breached=True,
                action=f"REDUCE: {(1-self.reduce_factor)*100:.0f}% 축소 ({dd*100:.1f}% < {self.reduce_pct*100:.0f}% 한도)",
                reduction_factor=self.reduce_factor,
            )
        elif dd <= self.warning_pct:
            return DrawdownState(
                current_value=current_value,
                peak_value=peak_value,
                drawdown_pct=dd,
                is_breached=False,
                action=f"WARNING: 신규 매수 중단 ({dd*100:.1f}% < {self.warning_pct*100:.0f}% 경고)",
                reduction_factor=1.0,
            )
        else:
            return DrawdownState(
                current_value=current_value,
                peak_value=peak_value,
                drawdown_pct=dd,
                is_breached=False,
                action="NORMAL",
                reduction_factor=1.0,
            )

    def track(self, equity_curve: List[float]) -> List[DrawdownState]:
        """자산 곡선 전체에 대해 드로다운 추적

        Args:
            equity_curve: 일별 포트폴리오 가치 리스트
        """
        states = []
        peak = 0.0

        for value in equity_curve:
            peak = max(peak, value)
            states.append(self.check(value, peak))

        return states

    def max_drawdown(self, equity_curve: List[float]) -> float:
        """최대 드로다운 계산 (음수 반환)"""
        if not equity_curve:
            return 0.0

        peak = equity_curve[0]
        max_dd = 0.0

        for value in equity_curve:
            peak = max(peak, value)
            if peak > 0:
                dd = (value - peak) / peak
                max_dd = min(max_dd, dd)

        return max_dd


@dataclass(frozen=True)
class ConcentrationLimits:
    """포트폴리오 집중도 한도"""
    max_single_stock: float = 0.15    # 단일 종목 최대 15%
    max_single_sector: float = 0.35   # 단일 섹터 최대 35%
    max_correlation: float = 0.60     # 포트폴리오 평균 상관 최대 0.6


def check_concentration(
    weights: dict[str, float],
    sectors: Optional[dict[str, str]] = None,
    limits: Optional[ConcentrationLimits] = None,
) -> dict[str, list[str]]:
    """포트폴리오 집중도 검증

    Returns:
        {"violations": [...], "warnings": [...]}
    """
    limits = limits or ConcentrationLimits()
    violations: list[str] = []
    warnings: list[str] = []

    # 단일 종목 집중도
    for ticker, weight in weights.items():
        if abs(weight) > limits.max_single_stock:
            violations.append(
                f"종목 집중도 초과: {ticker} = {weight*100:.1f}% > {limits.max_single_stock*100:.0f}%"
            )

    # 섹터 집중도
    if sectors:
        sector_weights: dict[str, float] = {}
        for ticker, weight in weights.items():
            sector = sectors.get(ticker, "Unknown")
            sector_weights[sector] = sector_weights.get(sector, 0.0) + abs(weight)

        for sector, total in sector_weights.items():
            if total > limits.max_single_sector:
                violations.append(
                    f"섹터 집중도 초과: {sector} = {total*100:.1f}% > {limits.max_single_sector*100:.0f}%"
                )

    return {"violations": violations, "warnings": warnings}
