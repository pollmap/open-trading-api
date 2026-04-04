"""변동성 타겟팅 + 터뷸런스 감지

Moskowitz, Ooi & Pedersen (2012) "Time Series Momentum":
  Weight_i = (target_vol / estimated_vol_i) x signal_i

Kim, Tse & Wald (2016): 모멘텀 알파의 대부분은 변동성 스케일링에서 온다.
Kritzman & Li (2010): 터뷸런스 인덱스 = Mahalanobis distance로 위기 조기 감지.

이 모듈은 모든 전략의 포지션을 변동성 기준으로 정규화한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass
class VolTargetResult:
    """변동성 타겟팅 결과"""
    raw_weight: float           # 원래 시그널 가중치
    vol_scaled_weight: float    # 변동성 조정 가중치
    estimated_vol: float        # 추정 변동성 (연율)
    target_vol: float           # 목표 변동성
    scale_factor: float         # 스케일링 배수


class VolatilityTargeter:
    """변동성 타겟팅

    모든 주요 퀀트 펀드의 표준 방법론.
    각 종목/전략의 포지션을 변동성 기준으로 정규화하여
    동일한 리스크 기여도를 보장한다.

    Usage:
        targeter = VolatilityTargeter(target_vol=0.10)

        # 단일 종목
        result = targeter.scale(
            raw_weight=0.20,
            returns=daily_returns_list,
        )
        print(f"조정 비중: {result.vol_scaled_weight:.3f}")

        # 포트폴리오 전체
        weights = targeter.scale_portfolio(
            raw_weights={"삼성": 0.3, "SK": 0.3, "LG": 0.4},
            returns_dict={"삼성": [...], "SK": [...], "LG": [...]},
        )
    """

    def __init__(
        self,
        target_vol: float = 0.10,
        lookback: int = 60,
        annualization: float = 252.0,
        max_leverage: float = 2.0,
        ewma_lambda: float = 0.94,
    ):
        """
        Args:
            target_vol: 목표 연간 변동성 (0.10 = 10%)
            lookback: 변동성 추정 기간 (거래일)
            annualization: 연율화 계수 (252 = 일간)
            max_leverage: 최대 레버리지 (개인 = 1.0~2.0)
            ewma_lambda: EWMA 감쇠 계수 (RiskMetrics 표준 = 0.94)
        """
        self.target_vol = target_vol
        self.lookback = lookback
        self.annualization = annualization
        self.max_leverage = max_leverage
        self.ewma_lambda = ewma_lambda

    def estimate_vol(
        self,
        returns: Sequence[float],
        method: str = "ewma",
    ) -> float:
        """변동성 추정 (연율화)

        Args:
            returns: 일간 수익률 리스트
            method: "simple" (단순 표준편차) 또는 "ewma" (지수가중)
        """
        if len(returns) < 2:
            return 0.0

        recent = list(returns[-self.lookback:])
        n = len(recent)

        if method == "ewma":
            lam = self.ewma_lambda
            weights = [(1 - lam) * lam ** i for i in range(n)]
            weights.reverse()
            w_sum = sum(weights)
            mean = sum(r * w for r, w in zip(recent, weights)) / w_sum
            variance = sum(w * (r - mean) ** 2 for r, w in zip(recent, weights)) / w_sum
        else:
            mean = sum(recent) / n
            variance = sum((r - mean) ** 2 for r in recent) / (n - 1)

        daily_vol = math.sqrt(variance)
        return daily_vol * math.sqrt(self.annualization)

    def scale(
        self,
        raw_weight: float,
        returns: Sequence[float],
        method: str = "ewma",
    ) -> VolTargetResult:
        """단일 종목 변동성 타겟팅

        Weight_scaled = raw_weight × (target_vol / estimated_vol)
        단, max_leverage로 상한 제한.
        """
        est_vol = self.estimate_vol(returns, method)

        if est_vol <= 0:
            return VolTargetResult(
                raw_weight=raw_weight,
                vol_scaled_weight=raw_weight,
                estimated_vol=0.0,
                target_vol=self.target_vol,
                scale_factor=1.0,
            )

        scale = self.target_vol / est_vol
        scale = min(scale, self.max_leverage)

        scaled_weight = raw_weight * scale

        return VolTargetResult(
            raw_weight=raw_weight,
            vol_scaled_weight=scaled_weight,
            estimated_vol=est_vol,
            target_vol=self.target_vol,
            scale_factor=scale,
        )

    def scale_portfolio(
        self,
        raw_weights: dict[str, float],
        returns_dict: dict[str, Sequence[float]],
        method: str = "ewma",
    ) -> dict[str, VolTargetResult]:
        """포트폴리오 전체 변동성 타겟팅"""
        results = {}
        for ticker, weight in raw_weights.items():
            rets = returns_dict.get(ticker, [])
            results[ticker] = self.scale(weight, rets, method)
        return results


def turbulence_index(
    current_returns: Sequence[float],
    historical_returns: List[Sequence[float]],
) -> float:
    """터뷸런스 인덱스 (Kritzman & Li, 2010)

    Mahalanobis distance로 현재 수익률 벡터가 과거 분포에서
    얼마나 이상한지 측정. 높을수록 위기 상황.

    간소화 버전: 상관행렬 대신 분산만 사용 (단변량 근사).
    전체 버전은 MCP portadv_rmt_clean과 결합하여 사용.

    Args:
        current_returns: 현재 기간의 자산별 수익률
        historical_returns: 과거 기간들의 자산별 수익률 리스트

    Returns:
        터뷸런스 지수 (> 1.0 이면 평균 이상 스트레스)
    """
    if not historical_returns or not current_returns:
        return 0.0

    n_assets = len(current_returns)
    n_periods = len(historical_returns)

    if n_periods < 2:
        return 0.0

    # 과거 평균
    means = [0.0] * n_assets
    for period in historical_returns:
        for i in range(min(n_assets, len(period))):
            means[i] += period[i]
    means = [m / n_periods for m in means]

    # 과거 분산
    variances = [0.0] * n_assets
    for period in historical_returns:
        for i in range(min(n_assets, len(period))):
            variances[i] += (period[i] - means[i]) ** 2
    variances = [v / (n_periods - 1) for v in variances]

    # Mahalanobis-like distance (대각 근사)
    d_sq = 0.0
    for i in range(n_assets):
        if variances[i] > 0:
            d_sq += (current_returns[i] - means[i]) ** 2 / variances[i]

    # 평균 터뷸런스로 정규화 (1.0 = 평균)
    avg_turb = 0.0
    for period in historical_returns:
        t = 0.0
        for i in range(min(n_assets, len(period))):
            if variances[i] > 0:
                t += (period[i] - means[i]) ** 2 / variances[i]
        avg_turb += t
    avg_turb /= n_periods

    if avg_turb <= 0:
        return 0.0

    return d_sq / avg_turb
