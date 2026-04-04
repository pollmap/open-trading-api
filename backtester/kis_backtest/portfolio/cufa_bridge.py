"""CUFA 보고서 → 퀀트 파이프라인 브릿지

CUFA 기업분석보고서의 Kill Conditions, 투자포인트(IP)를
ReviewEngine KillCondition 데이터클래스와 KIS 백테스트 전략으로 변환한다.

Usage:
    from kis_backtest.portfolio.cufa_bridge import CUFABridge
    from kis_backtest.portfolio.review_engine import KillCondition

    # CUFA 보고서에서 Kill Conditions 추출
    report = {"kill_conditions": [...], "investment_points": [...]}
    kc_list = CUFABridge.parse_kill_conditions(report)

    # DART 실시간 데이터로 평가
    kc_list = CUFABridge.evaluate_kill_conditions(kc_list, mcp_provider)

    # IP → 백테스트 전략 매핑
    strategies = CUFABridge.extract_strategy_from_ip(report)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kis_backtest.portfolio.review_engine import KillCondition

if TYPE_CHECKING:
    from kis_backtest.portfolio.mcp_data_provider import MCPDataProvider

logger = logging.getLogger(__name__)

# IP 유형 → KIS 백테스트 전략 매핑
# CUFA SKILL.md lines 3809-3818 기반
IP_STRATEGY_MAP: Dict[str, List[str]] = {
    "growth": ["sma_crossover", "momentum"],
    "capa": ["sma_crossover", "momentum"],
    "momentum": ["week52_high", "volatility_breakout"],
    "value": ["ma_divergence", "short_term_reversal"],
    "surprise": ["strong_close", "consecutive_moves"],
    "turnaround": ["short_term_reversal", "false_breakout"],
    "dividend": ["trend_filter_signal", "sma_crossover"],
    "stability": ["trend_filter_signal", "sma_crossover"],
}

# Kill Condition metric → DART 재무비율 필드 매핑
METRIC_DART_MAP: Dict[str, str] = {
    "revenue_growth": "revenue_growth_rate",
    "opm": "operating_profit_margin",
    "npm": "net_profit_margin",
    "roe": "roe",
    "debt_ratio": "debt_ratio",
    "current_ratio": "current_ratio",
    "interest_coverage": "interest_coverage_ratio",
    "fcf": "free_cash_flow",
    "dividend_payout": "dividend_payout_ratio",
    "capex_ratio": "capex_to_revenue",
}


class CUFABridge:
    """CUFA 보고서 결과를 퀀트 파이프라인 형식으로 변환"""

    @staticmethod
    def parse_kill_conditions(cufa_report: Dict[str, Any]) -> List[KillCondition]:
        """CUFA 보고서의 Kill Condition 테이블을 KillCondition 리스트로 변환

        Expected input format (CUFA SKILL.md lines 1146-1148):
        {
            "kill_conditions": [
                {
                    "condition": "OPM < 10% 2분기 연속",
                    "metric": "opm",
                    "trigger": 0.10,
                    "current": 0.132,
                    "margin": 0.032,
                    "frequency": "quarterly"
                },
                ...
            ]
        }
        """
        raw_kcs = cufa_report.get("kill_conditions", [])
        if not raw_kcs:
            return []

        result = []
        for kc in raw_kcs:
            if not isinstance(kc, dict):
                continue

            description = kc.get("condition", kc.get("description", ""))
            metric = kc.get("metric", "")
            threshold = kc.get("trigger", kc.get("threshold", 0.0))
            current = kc.get("current", kc.get("current_value"))

            if not description or not metric:
                continue

            # 현재값이 있으면 트리거 여부 자동 판정
            is_triggered = False
            if current is not None:
                is_triggered = _check_trigger(metric, float(threshold), float(current))

            result.append(KillCondition(
                description=description,
                metric=metric,
                threshold=float(threshold),
                current_value=float(current) if current is not None else None,
                is_triggered=is_triggered,
            ))

        return result

    @staticmethod
    def evaluate_kill_conditions(
        kill_conditions: List[KillCondition],
        mcp_provider: "MCPDataProvider",
        ticker: Optional[str] = None,
    ) -> List[KillCondition]:
        """DART 실시간 재무데이터로 Kill Conditions 재평가

        MCP dart_financial_ratios를 호출하여 current_value를 업데이트하고
        is_triggered를 재판정한다.
        """
        if not kill_conditions or not ticker:
            return kill_conditions

        try:
            financials = mcp_provider.get_dart_financials_sync(ticker, "CFS")
        except Exception as e:
            logger.warning("DART 재무비율 조회 실패, 기존 값 유지: %s", e)
            return kill_conditions

        if not financials:
            return kill_conditions

        updated = []
        for kc in kill_conditions:
            dart_field = METRIC_DART_MAP.get(kc.metric)
            new_value = financials.get(dart_field) if dart_field else None

            if new_value is not None:
                new_value = float(new_value)
                is_triggered = _check_trigger(kc.metric, kc.threshold, new_value)
                updated.append(KillCondition(
                    description=kc.description,
                    metric=kc.metric,
                    threshold=kc.threshold,
                    current_value=new_value,
                    is_triggered=is_triggered,
                ))
                if is_triggered:
                    logger.warning(
                        "KILL TRIGGERED: %s (현재: %.4f, 기준: %.4f)",
                        kc.description, new_value, kc.threshold,
                    )
            else:
                updated.append(kc)

        return updated

    @staticmethod
    def extract_strategy_from_ip(
        cufa_report: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """투자포인트(IP)를 KIS 백테스트 전략으로 매핑

        Expected input:
        {
            "investment_points": [
                {"id": 1, "title": "CAPA 확장", "type": "growth", "ticker": "005930"},
                {"id": 2, "title": "밸류에이션 매력", "type": "value", "ticker": "005930"},
            ]
        }

        Returns:
        [
            {
                "ip_id": 1,
                "ip_title": "CAPA 확장",
                "ip_type": "growth",
                "ticker": "005930",
                "strategies": ["sma_crossover", "momentum"],
            },
            ...
        ]
        """
        ips = cufa_report.get("investment_points", [])
        if not ips:
            return []

        result = []
        for ip in ips:
            if not isinstance(ip, dict):
                continue

            ip_type = ip.get("type", "").lower()
            strategies = IP_STRATEGY_MAP.get(ip_type, ["sma_crossover"])

            result.append({
                "ip_id": ip.get("id", 0),
                "ip_title": ip.get("title", ""),
                "ip_type": ip_type,
                "ticker": ip.get("ticker", ""),
                "strategies": strategies,
            })

        return result

    @staticmethod
    def three_stop_risk(
        position_size: float,
        adr_pct: float,
    ) -> Dict[str, float]:
        """3-Stop 리스크 관리 (CUFA SKILL.md lines 1149-1155)

        Jeff Sun 패턴: 1R 포지션에서 3단계 분할 손절
        → 최대 손실 -0.67R (전부 손절해도 -1R이 아닌 -0.67R)

        Args:
            position_size: 전체 포지션 금액
            adr_pct: Average Daily Range % (변동성 기반 손절폭)

        Returns:
            {
                "stop1_price_pct": 손절1 가격 거리 (%),
                "stop1_size": 1/3 포지션 금액,
                "stop2_price_pct": 손절2 가격 거리 (%),
                "stop2_size": 1/3 포지션 금액,
                "stop3_price_pct": 손절3 가격 거리 (%),
                "stop3_size": 1/3 포지션 금액,
                "max_loss_r": 최대 R 손실 (0.67),
                "max_loss_amount": 최대 손실 금액,
            }
        """
        third = position_size / 3.0

        stop1_pct = adr_pct * 1.0  # 1 ADR
        stop2_pct = adr_pct * 1.5  # 1.5 ADR (LoD + ATR)
        stop3_pct = adr_pct * 2.0  # 2 ADR

        # 각 stop의 실제 손실
        loss1 = third * stop1_pct  # 1/3 × 1 ADR
        loss2 = third * stop2_pct  # 1/3 × 1.5 ADR
        loss3 = third * stop3_pct  # 1/3 × 2 ADR
        total_loss = loss1 + loss2 + loss3

        # 1R = position_size × adr_pct
        one_r = position_size * adr_pct
        max_loss_r = total_loss / one_r if one_r > 0 else 0.0

        return {
            "stop1_price_pct": stop1_pct,
            "stop1_size": third,
            "stop2_price_pct": stop2_pct,
            "stop2_size": third,
            "stop3_price_pct": stop3_pct,
            "stop3_size": third,
            "max_loss_r": round(max_loss_r, 4),
            "max_loss_amount": round(total_loss, 0),
        }


def _check_trigger(metric: str, threshold: float, current: float) -> bool:
    """Kill Condition 트리거 판정

    대부분의 재무 메트릭은 "threshold 미만이면 위험":
    - opm < 10% → triggered
    - revenue_growth < 10% → triggered
    - roe < 5% → triggered

    예외 (높을수록 위험):
    - debt_ratio > 200% → triggered
    """
    high_is_bad = {"debt_ratio", "capex_ratio"}

    if metric in high_is_bad:
        return current > threshold
    else:
        return current < threshold
