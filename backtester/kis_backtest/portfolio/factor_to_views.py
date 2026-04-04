"""팩터 점수 → Black-Litterman 뷰 자동 변환

문제: BL에 수동으로 뷰를 넣으면 매번 사람이 개입해야 함.
해결: 팩터 composite 점수를 자동으로 BL 뷰 형식으로 변환.

원리:
  - 팩터 상위 종목 → 양의 기대수익 뷰
  - 팩터 하위 종목 → 0 또는 음의 기대수익 뷰 (롱온리 → 0)
  - 점수 크기 → confidence 매핑

References:
  - He & Litterman (1999), "The Intuition Behind BL"
  - Idzorek (2005), "Step-by-Step Guide to BL"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class BLView:
    """Black-Litterman 뷰"""
    asset: str          # 종목 이름
    ticker: str         # 종목 코드
    expected_return: float  # 기대 연간 수익률
    confidence: float   # 확신도 (0~1)


def factor_scores_to_bl_views(
    factor_scores: Dict[str, Dict],
    base_return: float = 0.08,
    spread: float = 0.10,
    min_confidence: float = 0.3,
    max_confidence: float = 0.9,
    long_only: bool = True,
) -> List[BLView]:
    """팩터 점수를 BL 뷰로 자동 변환

    Args:
        factor_scores: {ticker: {"name": str, "score": float, "sector": str}}
        base_return: 기본 기대수익률 (시장 평균). 팩터 0점 = 이 수익률
        spread: 팩터 1점당 기대수익률 변동 폭
        min_confidence: 최소 확신도
        max_confidence: 최대 확신도
        long_only: True면 음의 기대수익 뷰 제외 (개인투자자)

    Returns:
        BL 뷰 리스트

    Example:
        views = factor_scores_to_bl_views({
            "005930": {"name": "삼성전자", "score": 0.82},
            "000660": {"name": "SK하이닉스", "score": -0.50},
        })
        # → [BLView("삼성전자", "005930", 0.162, 0.82), ...]
    """
    if not factor_scores:
        return []

    # 점수 범위 계산 (정규화용)
    scores = [info.get("score", 0) for info in factor_scores.values()]
    score_min = min(scores)
    score_max = max(scores)
    score_range = score_max - score_min if score_max != score_min else 1.0

    views = []
    for ticker, info in factor_scores.items():
        name = info.get("name", ticker)
        score = info.get("score", 0)

        # 점수 → 기대수익률
        # 정규화: -1 ~ +1 범위로 매핑
        normalized = (score - (score_min + score_max) / 2) / (score_range / 2) if score_range > 0 else 0
        expected_return = base_return + normalized * spread

        # 롱온리: 음수 기대수익 → 스킵
        if long_only and expected_return <= 0:
            continue

        # 점수 절대값 → 확신도 (높을수록 확신)
        abs_normalized = abs(normalized)
        confidence = min_confidence + abs_normalized * (max_confidence - min_confidence)
        confidence = min(max(confidence, min_confidence), max_confidence)

        views.append(BLView(
            asset=name,
            ticker=ticker,
            expected_return=round(expected_return, 4),
            confidence=round(confidence, 3),
        ))

    return views


def bl_views_to_mcp_format(views: List[BLView]) -> List[Dict]:
    """BL 뷰를 MCP portadv_black_litterman 입력 형식으로 변환

    Returns:
        [{"asset": "삼성전자", "return": 0.15, "confidence": 0.8}, ...]
    """
    return [
        {
            "asset": v.asset,
            "return": v.expected_return,
            "confidence": v.confidence,
        }
        for v in views
    ]


def views_summary(views: List[BLView]) -> str:
    """뷰 요약 문자열"""
    if not views:
        return "뷰 없음"

    lines = ["=== BL Views (팩터 자동 생성) ==="]
    for v in sorted(views, key=lambda x: -x.expected_return):
        lines.append(
            f"  {v.asset:>10} ({v.ticker}): "
            f"E[R]={v.expected_return*100:+.1f}%, "
            f"conf={v.confidence:.0%}"
        )
    return "\n".join(lines)
