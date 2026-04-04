"""상관관계 모니터

위기 시 모든 자산의 상관이 1로 수렴하는 현상을 감지.
Longin & Solnik (2001): 하락장에서 상관 비대칭 상승.
Forbes & Rigobon (2002): 변동성 증가 → 상관 편향 상승.

DCC-GARCH는 과잉이므로 rolling correlation + 경보 시스템으로 구현.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass
class CorrelationAlert:
    """상관관계 경보"""
    avg_correlation: float      # 포트폴리오 평균 상관
    max_pair: Tuple[str, str]   # 최고 상관 페어
    max_correlation: float      # 최고 상관 값
    is_elevated: bool           # 경보 수준 (> threshold)
    message: str


class CorrelationMonitor:
    """포트폴리오 상관관계 모니터

    Usage:
        monitor = CorrelationMonitor(threshold=0.6, lookback=60)

        alert = monitor.check(
            returns_dict={"삼성전자": [...], "SK하이닉스": [...], ...},
            weights={"삼성전자": 0.15, "SK하이닉스": 0.12, ...},
        )

        if alert.is_elevated:
            print(alert.message)
    """

    def __init__(
        self,
        threshold: float = 0.6,
        critical_threshold: float = 0.8,
        lookback: int = 60,
    ):
        self.threshold = threshold
        self.critical_threshold = critical_threshold
        self.lookback = lookback

    def _pearson(self, x: Sequence[float], y: Sequence[float]) -> float:
        """Pearson 상관계수 계산"""
        n = min(len(x), len(y))
        if n < 5:
            return 0.0

        x_vals = list(x[-n:])
        y_vals = list(y[-n:])

        mean_x = sum(x_vals) / n
        mean_y = sum(y_vals) / n

        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x_vals, y_vals)) / (n - 1)
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x_vals) / (n - 1))
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y_vals) / (n - 1))

        if std_x == 0 or std_y == 0:
            return 0.0

        return cov / (std_x * std_y)

    def check(
        self,
        returns_dict: Dict[str, Sequence[float]],
        weights: Optional[Dict[str, float]] = None,
    ) -> CorrelationAlert:
        """포트폴리오 상관관계 체크

        Args:
            returns_dict: {ticker: 일간 수익률 리스트}
            weights: {ticker: 비중} (가중 평균 상관 계산용, 없으면 균등)
        """
        tickers = [t for t in returns_dict if len(returns_dict[t]) >= self.lookback]

        if len(tickers) < 2:
            return CorrelationAlert(
                avg_correlation=0.0,
                max_pair=("", ""),
                max_correlation=0.0,
                is_elevated=False,
                message="종목 부족 (2개 미만)",
            )

        # 모든 페어 상관 계산
        correlations = []
        max_corr = -1.0
        max_pair = ("", "")

        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                t1, t2 = tickers[i], tickers[j]
                r1 = returns_dict[t1][-self.lookback:]
                r2 = returns_dict[t2][-self.lookback:]
                corr = self._pearson(r1, r2)
                correlations.append(corr)

                if corr > max_corr:
                    max_corr = corr
                    max_pair = (t1, t2)

        avg_corr = sum(correlations) / len(correlations) if correlations else 0.0

        # 경보 판정
        if avg_corr >= self.critical_threshold:
            msg = f"CRITICAL: 평균 상관 {avg_corr:.2f} >= {self.critical_threshold:.1f} — 분산 효과 소멸, 즉시 비중 축소"
        elif avg_corr >= self.threshold:
            msg = f"WARNING: 평균 상관 {avg_corr:.2f} >= {self.threshold:.1f} — 분산 효과 약화"
        else:
            msg = f"OK: 평균 상관 {avg_corr:.2f} (한도 {self.threshold:.1f} 이내)"

        return CorrelationAlert(
            avg_correlation=round(avg_corr, 4),
            max_pair=max_pair,
            max_correlation=round(max_corr, 4),
            is_elevated=avg_corr >= self.threshold,
            message=msg,
        )

    def correlation_matrix(
        self,
        returns_dict: Dict[str, Sequence[float]],
    ) -> Dict[str, Dict[str, float]]:
        """전체 상관 행렬 반환"""
        tickers = [t for t in returns_dict if len(returns_dict[t]) >= self.lookback]
        matrix = {}

        for t1 in tickers:
            matrix[t1] = {}
            for t2 in tickers:
                if t1 == t2:
                    matrix[t1][t2] = 1.0
                else:
                    matrix[t1][t2] = self._pearson(
                        returns_dict[t1][-self.lookback:],
                        returns_dict[t2][-self.lookback:],
                    )

        return matrix
