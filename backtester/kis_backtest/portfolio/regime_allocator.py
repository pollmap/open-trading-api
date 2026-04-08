"""레짐 기반 자산배분기 — Druckenmiller의 "큰 그림이 맞으면 종목은 덜 중요하다"

매크로 레짐(확장/수축/위기/회복) 판별 결과를 받아 포트폴리오 자산배분을 자동 조정한다.
한국 ETF 매핑으로 실제 리밸런싱 주문까지 생성.

Usage:
    from kis_backtest.portfolio.regime_allocator import RegimeAllocator
    from kis_backtest.portfolio.macro_regime import Regime

    allocator = RegimeAllocator(total_capital=100_000_000)

    # 레짐에 따른 배분 계획
    plan = allocator.allocate(Regime.EXPANSION)
    print(plan.summary())

    # 기존 보유에서 리밸런싱 주문 생성
    orders = allocator.rebalance(
        Regime.CRISIS,
        current_holdings={"069500": 70_000_000, "CASH": 30_000_000},
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from kis_backtest.portfolio.macro_regime import REGIME_ALLOCATION, Regime

logger = logging.getLogger(__name__)


# ── 자산 클래스 ──────────────────────────────────────────────


class AssetClass(str, Enum):
    """투자 가능 자산 클래스"""

    EQUITY = "equity"
    BOND = "bond"
    GOLD = "gold"
    CRYPTO = "crypto"
    CASH = "cash"
    INVERSE = "inverse"


# ── 한국 ETF 매핑 ────────────────────────────────────────────

DEFAULT_ETF_MAP: Dict[AssetClass, str] = {
    AssetClass.EQUITY: "069500",       # KODEX 200
    AssetClass.BOND: "148070",         # KOSEF 국고채10년
    AssetClass.GOLD: "132030",         # KODEX 골드선물
    AssetClass.CRYPTO: "NOT_AVAILABLE",  # Upbit 직접 사용
    AssetClass.CASH: "CASH",           # ETF 불필요
    AssetClass.INVERSE: "252670",      # KODEX 200선물인버스2X
}


# ── 데이터 클래스 ────────────────────────────────────────────


@dataclass(frozen=True)
class AllocationTarget:
    """단일 자산 배분 목표

    Attributes:
        asset_class: 자산 클래스
        weight: 배분 비중 (0.0 ~ 1.0)
        etf_ticker: 한국 ETF 코드
        description: 설명
    """

    asset_class: AssetClass
    weight: float
    etf_ticker: Optional[str] = None
    description: str = ""


@dataclass(frozen=True)
class RebalanceOrder:
    """리밸런싱 주문

    Attributes:
        asset_class: 자산 클래스
        etf_ticker: ETF 종목코드
        action: "buy" 또는 "sell"
        amount: 금액 (KRW)
        reason: 주문 사유
    """

    asset_class: AssetClass
    etf_ticker: str
    action: str
    amount: float
    reason: str


@dataclass(frozen=True)
class AllocationPlan:
    """레짐 기반 자산배분 계획

    Attributes:
        regime: 매크로 레짐
        targets: 자산별 배분 목표 리스트
        total_capital: 총 운용 자본 (KRW)
        created_at: 생성 시각
    """

    regime: Regime
    targets: tuple[AllocationTarget, ...]  # frozen 호환을 위해 tuple
    total_capital: float
    created_at: str

    @property
    def weights_sum(self) -> float:
        """배분 비중 합계 (1.0에 근접해야 함)"""
        return sum(t.weight for t in self.targets)

    def summary(self) -> str:
        """배분 계획 요약 문자열"""
        lines = [
            f"[AllocationPlan] 레짐={self.regime.value.upper()} "
            f"자본={self.total_capital:,.0f}원 "
            f"생성={self.created_at}",
        ]
        for t in self.targets:
            amount = self.total_capital * t.weight
            ticker_str = f" ({t.etf_ticker})" if t.etf_ticker else ""
            lines.append(
                f"  {t.asset_class.value:<8s} {t.weight:6.1%} = "
                f"{amount:>15,.0f}원{ticker_str}"
            )
        lines.append(f"  합계: {self.weights_sum:.2%}")
        return "\n".join(lines)

    def to_orders(
        self, current_holdings: Dict[str, float]
    ) -> List[RebalanceOrder]:
        """현재 보유 대비 리밸런싱 주문 생성

        Args:
            current_holdings: {etf_ticker: 보유금액(KRW)} 딕셔너리

        Returns:
            매수/매도 주문 리스트
        """
        orders: List[RebalanceOrder] = []
        for target in self.targets:
            ticker = target.etf_ticker or DEFAULT_ETF_MAP.get(
                target.asset_class, "UNKNOWN"
            )
            target_amount = self.total_capital * target.weight
            current_amount = current_holdings.get(ticker, 0.0)
            diff = target_amount - current_amount

            if abs(diff) < 1.0:
                continue

            action = "buy" if diff > 0 else "sell"
            reason = (
                f"{self.regime.value} 레짐: "
                f"{target.asset_class.value} {target.weight:.0%} 목표 → "
                f"현재 {current_amount:,.0f}원 → 목표 {target_amount:,.0f}원"
            )
            orders.append(
                RebalanceOrder(
                    asset_class=target.asset_class,
                    etf_ticker=ticker,
                    action=action,
                    amount=abs(diff),
                    reason=reason,
                )
            )
        return orders


# ── 메인 클래스 ──────────────────────────────────────────────


class RegimeAllocator:
    """레짐 기반 자산배분기

    매크로 레짐에 따라 자산배분 계획을 생성하고
    현재 보유 대비 리밸런싱 주문을 산출한다.

    Args:
        total_capital: 총 운용 자본 (KRW)
        custom_allocations: 커스텀 레짐별 배분 (기본: REGIME_ALLOCATION 사용)
    """

    def __init__(
        self,
        total_capital: float,
        custom_allocations: Optional[Dict[Regime, Dict[str, float]]] = None,
    ) -> None:
        if total_capital <= 0:
            raise ValueError(f"total_capital must be positive, got {total_capital}")
        self._total_capital = total_capital
        self._allocations: Dict[Regime, Dict[str, float]] = (
            dict(custom_allocations) if custom_allocations else dict(REGIME_ALLOCATION)
        )

    @property
    def total_capital(self) -> float:
        return self._total_capital

    @property
    def allocations(self) -> Dict[Regime, Dict[str, float]]:
        return dict(self._allocations)

    def allocate(self, regime: Regime) -> AllocationPlan:
        """레짐에 따른 자산배분 계획 생성

        Args:
            regime: 매크로 레짐

        Returns:
            AllocationPlan 인스턴스

        Raises:
            KeyError: 해당 레짐의 배분이 정의되지 않은 경우
        """
        alloc = self._allocations.get(regime)
        if alloc is None:
            raise KeyError(f"No allocation defined for regime: {regime.value}")

        targets: List[AllocationTarget] = []
        for asset_name, weight in alloc.items():
            asset_class = AssetClass(asset_name)
            etf_ticker = DEFAULT_ETF_MAP.get(asset_class)
            description = _asset_description(asset_class, regime)
            targets.append(
                AllocationTarget(
                    asset_class=asset_class,
                    weight=weight,
                    etf_ticker=etf_ticker,
                    description=description,
                )
            )

        plan = AllocationPlan(
            regime=regime,
            targets=tuple(targets),
            total_capital=self._total_capital,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )

        # 비중 합계 검증
        assert abs(plan.weights_sum - 1.0) < 0.01, (
            f"Weights do not sum to ~1.0: {plan.weights_sum:.4f}"
        )

        logger.info(
            "배분 계획 생성: regime=%s, targets=%d, capital=%,.0f",
            regime.value,
            len(targets),
            self._total_capital,
        )
        return plan

    def rebalance(
        self,
        regime: Regime,
        current_holdings: Dict[str, float],
    ) -> List[RebalanceOrder]:
        """레짐에 따른 리밸런싱 주문 생성

        Args:
            regime: 매크로 레짐
            current_holdings: {etf_ticker: 보유금액(KRW)}

        Returns:
            RebalanceOrder 리스트
        """
        plan = self.allocate(regime)
        orders = plan.to_orders(current_holdings)
        logger.info(
            "리밸런싱 주문 %d건 생성: regime=%s",
            len(orders),
            regime.value,
        )
        return orders

    def transition_summary(
        self, old_regime: Regime, new_regime: Regime
    ) -> str:
        """레짐 전환 요약

        Args:
            old_regime: 이전 레짐
            new_regime: 새 레짐

        Returns:
            전환 요약 문자열
        """
        old_alloc = self._allocations.get(old_regime, {})
        new_alloc = self._allocations.get(new_regime, {})

        all_assets = sorted(set(old_alloc.keys()) | set(new_alloc.keys()))

        lines = [
            f"레짐 전환: {old_regime.value.upper()} → {new_regime.value.upper()}",
            f"운용 자본: {self._total_capital:,.0f}원",
            "",
            f"{'자산':<10s} {'이전':>8s} {'이후':>8s} {'변화':>8s} {'금액변화':>15s}",
            "-" * 55,
        ]

        for asset_name in all_assets:
            old_w = old_alloc.get(asset_name, 0.0)
            new_w = new_alloc.get(asset_name, 0.0)
            diff_w = new_w - old_w
            diff_amount = diff_w * self._total_capital

            arrow = "↑" if diff_w > 0 else ("↓" if diff_w < 0 else "─")
            lines.append(
                f"{asset_name:<10s} {old_w:>7.0%} {new_w:>7.0%} "
                f"{diff_w:>+7.0%}{arrow} {diff_amount:>+14,.0f}원"
            )

        return "\n".join(lines)


# ── 내부 유틸 ────────────────────────────────────────────────


def _asset_description(asset_class: AssetClass, regime: Regime) -> str:
    """자산 클래스 + 레짐에 따른 설명 생성"""
    descriptions: Dict[AssetClass, Dict[Regime, str]] = {
        AssetClass.EQUITY: {
            Regime.EXPANSION: "확장기 공격적 주식 비중",
            Regime.CONTRACTION: "수축기 방어적 주식 축소",
            Regime.CRISIS: "위기 시 주식 전량 매도",
            Regime.RECOVERY: "회복기 주식 비중 확대",
        },
        AssetClass.BOND: {
            Regime.CONTRACTION: "수축기 안전자산 채권 확대",
            Regime.RECOVERY: "회복기 채권 유지",
        },
        AssetClass.GOLD: {
            Regime.CONTRACTION: "수축기 인플레 헤지",
            Regime.CRISIS: "위기 시 안전자산 금 확대",
        },
        AssetClass.CRYPTO: {
            Regime.EXPANSION: "확장기 고위험 크립토 배분",
            Regime.RECOVERY: "회복기 크립토 소량 배분",
        },
        AssetClass.CASH: {
            Regime.EXPANSION: "확장기 최소 현금",
            Regime.CONTRACTION: "수축기 현금 확보",
            Regime.CRISIS: "위기 시 현금 최대 보유",
            Regime.RECOVERY: "회복기 적정 현금",
        },
        AssetClass.INVERSE: {
            Regime.CRISIS: "위기 시 인버스 헤지",
        },
    }
    return descriptions.get(asset_class, {}).get(regime, f"{asset_class.value} ({regime.value})")
