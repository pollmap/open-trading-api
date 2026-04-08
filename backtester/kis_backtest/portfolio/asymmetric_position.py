"""비대칭 포지션 설계 — Druckenmiller의 "얼마나 버느냐가 중요하다" 시스템

잃을 때는 적게, 벌 때는 크게. 최대 손실은 제한하되 최대 수익은 제한하지 않는
비대칭 포지션 구조를 설계한다.

Stan Druckenmiller 철학:
  "It's not about being right or wrong,
   but how much you make when you're right."

지원하는 비대칭 구조:
  - LEVERAGED_ETF: 레버리지 ETF (2x/3x)
  - INVERSE_ETF: 인버스 ETF 헤지
  - CRYPTO_CARRY: 암호화폐 현물+선물 펀딩레이트 차익
  - BARBELL: 탈레브式 바벨 (90% 안전 + 10% 고위험)

Usage:
    from kis_backtest.portfolio.asymmetric_position import (
        AsymmetricDesigner,
        AsymmetricPosition,
    )

    designer = AsymmetricDesigner()

    # 레버리지 ETF 포지션
    pos = designer.design_leveraged_etf("005930", capital=10_000_000, leverage=2)
    print(pos.summary())

    # 바벨 전략
    barbell = designer.design_barbell(capital=100_000_000)
    print(barbell.is_asymmetric)  # True

    # 리스크/리워드 평가
    metrics = designer.evaluate_risk_reward(pos)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# Korean ETF tickers
# ────────────────────────────────────────────────────────────────
TICKER_LEVERAGED_2X = "252710"  # KODEX 200 선물레버리지
TICKER_INVERSE_2X = "252670"    # KODEX 200선물인버스2X
TICKER_BOND_10Y = "148070"      # KOSEF 국고채10년
TICKER_GOLD = "132030"          # KODEX 골드선물


# ────────────────────────────────────────────────────────────────
# Enums
# ────────────────────────────────────────────────────────────────
class AsymmetryType(str, Enum):
    """비대칭 포지션 유형."""

    SPOT_ONLY = "spot_only"
    LEVERAGED_ETF = "leveraged_etf"
    INVERSE_ETF = "inverse_etf"
    CRYPTO_CARRY = "crypto_carry"
    BARBELL = "barbell"


# ────────────────────────────────────────────────────────────────
# Dataclasses
# ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PositionComponent:
    """포지션을 구성하는 개별 인스트루먼트."""

    instrument: str          # e.g. "069500", "BTC-KRW"
    direction: str           # "long" | "short"
    weight: float            # capital_at_risk 대비 비중 (0.0 ~ 1.0)
    leverage: float = 1.0    # 현물 = 1.0
    description: str = ""


@dataclass(frozen=True)
class AsymmetricPosition:
    """비대칭 포지션 — 최대 손실 제한, 최대 수익 무제한."""

    symbol: str
    name: str
    asymmetry_type: AsymmetryType
    capital_at_risk: float       # 최대 손실 (KRW)
    potential_upside: float      # 예상 최대 수익 (KRW), float('inf') 가능
    risk_reward_ratio: float     # upside / risk (높을수록 비대칭)
    components: List[PositionComponent] = field(default_factory=list)
    description: str = ""

    @property
    def is_asymmetric(self) -> bool:
        """risk_reward_ratio > 2.0 이면 진정한 비대칭."""
        return self.risk_reward_ratio > 2.0

    def summary(self) -> str:
        """포지션 요약 문자열."""
        upside_str = (
            "무제한" if math.isinf(self.potential_upside) else f"{self.potential_upside:,.0f}"
        )
        lines = [
            f"[{self.asymmetry_type.value}] {self.name} ({self.symbol})",
            f"  최대 손실: {self.capital_at_risk:,.0f} KRW",
            f"  예상 수익: {upside_str} KRW",
            f"  R:R 비율: {self.risk_reward_ratio:.2f}x",
            f"  비대칭 여부: {'Yes' if self.is_asymmetric else 'No'}",
            f"  구성: {len(self.components)}개 레그",
        ]
        if self.description:
            lines.append(f"  설명: {self.description}")
        return "\n".join(lines)


# ────────────────────────────────────────────────────────────────
# Designer
# ────────────────────────────────────────────────────────────────
class AsymmetricDesigner:
    """비대칭 포지션 설계자.

    Druckenmiller 원칙을 적용하여 다양한 비대칭 구조를 설계한다.
    """

    # 기본 시장 변동폭 가정 (연간, 상승 시나리오)
    DEFAULT_MARKET_MOVE: float = 0.30  # 30%

    def design_leveraged_etf(
        self,
        symbol: str,
        capital: float,
        leverage: int = 2,
        *,
        expected_move: float | None = None,
    ) -> AsymmetricPosition:
        """레버리지 ETF 포지션 설계.

        Args:
            symbol: 기초자산 종목코드
            capital: 투자 금액 (KRW)
            leverage: 레버리지 배수 (2 또는 3)
            expected_move: 예상 시장 변동폭 (기본 30%)

        Returns:
            AsymmetricPosition

        Raises:
            ValueError: capital <= 0 또는 leverage < 1
        """
        if capital <= 0:
            raise ValueError(f"capital must be positive, got {capital}")
        if leverage < 1:
            raise ValueError(f"leverage must be >= 1, got {leverage}")

        move = expected_move if expected_move is not None else self.DEFAULT_MARKET_MOVE
        potential = capital * leverage * move
        ratio = potential / capital if capital > 0 else 0.0

        etf_ticker = TICKER_LEVERAGED_2X  # 2x 기본
        etf_name = f"KODEX 200 선물레버리지 {leverage}X"

        component = PositionComponent(
            instrument=etf_ticker,
            direction="long",
            weight=1.0,
            leverage=float(leverage),
            description=f"{etf_name} 매수",
        )

        logger.info(
            "레버리지 ETF 포지션 설계: %s, 자본=%s, 레버리지=%dx, R:R=%.2f",
            symbol, capital, leverage, ratio,
        )

        return AsymmetricPosition(
            symbol=symbol,
            name=f"{symbol} 레버리지 {leverage}X",
            asymmetry_type=AsymmetryType.LEVERAGED_ETF,
            capital_at_risk=capital,
            potential_upside=potential,
            risk_reward_ratio=ratio,
            components=[component],
            description=(
                f"최대 손실 = 투입 자본 {capital:,.0f}원, "
                f"예상 수익 = {potential:,.0f}원 ({leverage}x × {move:.0%} 시장 상승 시)"
            ),
        )

    def design_crypto_carry(
        self,
        base_asset: str,
        capital: float,
        funding_rate_annual: float = 0.15,
    ) -> AsymmetricPosition:
        """암호화폐 캐리 트레이드 (현물 매수 + 선물 숏 → 펀딩레이트 수취).

        Args:
            base_asset: 기초자산 (e.g. "BTC-KRW")
            capital: 투자 금액 (KRW)
            funding_rate_annual: 연간 펀딩레이트 (기본 15%)

        Returns:
            AsymmetricPosition

        Raises:
            ValueError: capital <= 0 또는 funding_rate_annual < 0
        """
        if capital <= 0:
            raise ValueError(f"capital must be positive, got {capital}")
        if funding_rate_annual < 0:
            raise ValueError(
                f"funding_rate_annual must be >= 0, got {funding_rate_annual}"
            )

        # 수익 = 연간 펀딩레이트 × 자본
        annual_yield = capital * funding_rate_annual
        # 리스크 = 베이시스 리스크 (현물-선물 괴리), 자본의 ~5% 가정
        basis_risk = capital * 0.05
        ratio = annual_yield / basis_risk if basis_risk > 0 else 0.0

        spot_leg = PositionComponent(
            instrument=base_asset,
            direction="long",
            weight=0.5,
            leverage=1.0,
            description=f"{base_asset} 현물 매수",
        )
        futures_leg = PositionComponent(
            instrument=f"{base_asset}-PERP",
            direction="short",
            weight=0.5,
            leverage=1.0,
            description=f"{base_asset} 무기한선물 숏",
        )

        logger.info(
            "크립토 캐리 설계: %s, 자본=%s, 펀딩=%s, R:R=%.2f",
            base_asset, capital, funding_rate_annual, ratio,
        )

        return AsymmetricPosition(
            symbol=base_asset,
            name=f"{base_asset} 캐리 트레이드",
            asymmetry_type=AsymmetryType.CRYPTO_CARRY,
            capital_at_risk=basis_risk,
            potential_upside=annual_yield,
            risk_reward_ratio=ratio,
            components=[spot_leg, futures_leg],
            description=(
                f"현물 매수 + 선물 숏 → 연 {funding_rate_annual:.1%} 펀딩레이트 수취. "
                f"베이시스 리스크 ≈ {basis_risk:,.0f}원"
            ),
        )

    def design_barbell(
        self,
        safe_weight: float = 0.9,
        risky_weight: float = 0.1,
        capital: float = 1_000_000,
        *,
        risky_symbol: str = "005930",
        risky_name: str = "삼성전자",
    ) -> AsymmetricPosition:
        """탈레브式 바벨 전략: 안전자산 90% + 고확신 고위험 10%.

        Args:
            safe_weight: 안전자산 비중 (기본 0.9)
            risky_weight: 위험자산 비중 (기본 0.1)
            capital: 총 투자금 (KRW)
            risky_symbol: 고위험 종목코드
            risky_name: 고위험 종목명

        Returns:
            AsymmetricPosition

        Raises:
            ValueError: capital <= 0, weight 합 != 1.0, weight < 0
        """
        if capital <= 0:
            raise ValueError(f"capital must be positive, got {capital}")
        if safe_weight < 0 or risky_weight < 0:
            raise ValueError("weights must be non-negative")
        if not math.isclose(safe_weight + risky_weight, 1.0, abs_tol=1e-9):
            raise ValueError(
                f"weights must sum to 1.0, got {safe_weight + risky_weight}"
            )

        risky_capital = capital * risky_weight
        safe_capital = capital * safe_weight

        # 최대 손실 = 위험 포션 전액 + 채권 ~2% 하락
        max_loss = risky_capital + safe_capital * 0.02
        # 잠재 수익 = 위험 포션 무제한 (고확신 선별)
        potential = float("inf")
        ratio = potential / max_loss if max_loss > 0 else 0.0

        safe_leg = PositionComponent(
            instrument=TICKER_BOND_10Y,
            direction="long",
            weight=safe_weight,
            leverage=1.0,
            description=f"KOSEF 국고채10년 ({safe_weight:.0%})",
        )
        risky_leg = PositionComponent(
            instrument=risky_symbol,
            direction="long",
            weight=risky_weight,
            leverage=1.0,
            description=f"{risky_name} 고확신 매수 ({risky_weight:.0%})",
        )

        logger.info(
            "바벨 전략 설계: 안전=%s, 위험=%s(%s), 자본=%s",
            safe_weight, risky_weight, risky_symbol, capital,
        )

        return AsymmetricPosition(
            symbol=risky_symbol,
            name=f"바벨 ({risky_name} {risky_weight:.0%} + 채권 {safe_weight:.0%})",
            asymmetry_type=AsymmetryType.BARBELL,
            capital_at_risk=max_loss,
            potential_upside=potential,
            risk_reward_ratio=ratio,
            components=[safe_leg, risky_leg],
            description=(
                f"탈레브式 바벨: {safe_weight:.0%} 국고채 + {risky_weight:.0%} {risky_name}. "
                f"최대 손실 ≈ {max_loss:,.0f}원 (위험 포션 전액 + 채권 2% 하락)"
            ),
        )

    def design_inverse_hedge(
        self,
        portfolio_value: float,
        hedge_pct: float = 0.1,
    ) -> AsymmetricPosition:
        """인버스 ETF 헤지 — 포트폴리오 보험.

        Args:
            portfolio_value: 포트폴리오 총 가치 (KRW)
            hedge_pct: 헤지 비중 (기본 10%)

        Returns:
            AsymmetricPosition

        Raises:
            ValueError: portfolio_value <= 0 또는 hedge_pct 범위 초과
        """
        if portfolio_value <= 0:
            raise ValueError(
                f"portfolio_value must be positive, got {portfolio_value}"
            )
        if not 0 < hedge_pct <= 1.0:
            raise ValueError(f"hedge_pct must be in (0, 1.0], got {hedge_pct}")

        hedge_capital = portfolio_value * hedge_pct
        # 인버스 2X: 시장 20% 하락 시 ~40% 수익
        potential_gain = hedge_capital * 2.0 * 0.20  # 인버스2X × 20% 하락
        # 최대 손실 = 헤지 자본 전액 (시장 상승 시)
        max_loss = hedge_capital
        ratio = potential_gain / max_loss if max_loss > 0 else 0.0

        component = PositionComponent(
            instrument=TICKER_INVERSE_2X,
            direction="long",  # 인버스 ETF 매수 = 시장 숏
            weight=1.0,
            leverage=2.0,
            description="KODEX 200선물인버스2X 매수 (포트폴리오 보험)",
        )

        logger.info(
            "인버스 헤지 설계: 포트폴리오=%s, 헤지=%s, R:R=%.2f",
            portfolio_value, hedge_pct, ratio,
        )

        return AsymmetricPosition(
            symbol=TICKER_INVERSE_2X,
            name=f"포트폴리오 인버스 헤지 ({hedge_pct:.0%})",
            asymmetry_type=AsymmetryType.INVERSE_ETF,
            capital_at_risk=max_loss,
            potential_upside=potential_gain,
            risk_reward_ratio=ratio,
            components=[component],
            description=(
                f"포트폴리오 {portfolio_value:,.0f}원의 {hedge_pct:.0%} 인버스 헤지. "
                f"시장 20% 하락 시 {potential_gain:,.0f}원 수익"
            ),
        )

    def evaluate_risk_reward(
        self,
        position: AsymmetricPosition,
    ) -> Dict[str, Any]:
        """포지션의 리스크/리워드 메트릭 평가.

        Returns:
            Dict with keys:
                - risk_reward_ratio: float
                - is_asymmetric: bool
                - capital_at_risk: float
                - potential_upside: float (or 'inf')
                - max_loss_pct: float (0~1, vs capital_at_risk)
                - num_legs: int
                - asymmetry_type: str
                - grade: str ("S" | "A" | "B" | "C")
        """
        ratio = position.risk_reward_ratio

        if math.isinf(ratio) or ratio >= 10.0:
            grade = "S"
        elif ratio >= 5.0:
            grade = "A"
        elif ratio >= 2.0:
            grade = "B"
        else:
            grade = "C"

        upside_value: float | str = (
            "inf" if math.isinf(position.potential_upside) else position.potential_upside
        )

        return {
            "risk_reward_ratio": ratio,
            "is_asymmetric": position.is_asymmetric,
            "capital_at_risk": position.capital_at_risk,
            "potential_upside": upside_value,
            "max_loss_pct": 1.0,  # 최대 손실 = capital_at_risk 전액
            "num_legs": len(position.components),
            "asymmetry_type": position.asymmetry_type.value,
            "grade": grade,
        }
