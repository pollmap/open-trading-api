"""한국 증권 거래비용 모델

Renaissance Technologies의 "secret weapon"은 HMM이 아니라 트랜잭션 코스트 모델이었다.
한국은 매도 전용 증권거래세(0.20%)가 있어 비대칭 비용 구조.
이 모듈은 모든 전략의 백테스트/사이징/리밸런싱 판단의 기반.

References:
    - Almgren & Chriss (2000), "Optimal Execution of Portfolio Transactions"
    - Kelly (1956), "A New Interpretation of Information Rate"
    - 자본시장법 시행령 제178조 (증권거래세)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Market(str, Enum):
    """한국 거래소 구분"""
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"


@dataclass(frozen=True)
class KoreaFeeSchedule:
    """2026년 한국 증권 수수료/세금 스케줄

    매수: 증권사 수수료만
    매도: 증권사 수수료 + 증권거래세 + (KOSPI일 경우 농어촌특별세)
    """
    broker_commission: float = 0.00015    # 매수/매도 각 0.015% (온라인 기준)
    kospi_stt: float = 0.0005            # KOSPI 증권거래세 0.05%
    kospi_agri_tax: float = 0.0015       # KOSPI 농어촌특별세 0.15%
    kosdaq_stt: float = 0.0020           # KOSDAQ 증권거래세 0.20%

    @property
    def kospi_sell_tax(self) -> float:
        """KOSPI 매도 시 총 세금 = 증권거래세 + 농특세"""
        return self.kospi_stt + self.kospi_agri_tax  # 0.20%

    @property
    def kosdaq_sell_tax(self) -> float:
        """KOSDAQ 매도 시 총 세금"""
        return self.kosdaq_stt  # 0.20%


@dataclass
class TransactionCost:
    """단일 거래의 비용 분해"""
    broker_fee: float       # 증권사 수수료
    tax: float              # 증권거래세 + 농특세
    slippage: float         # 슬리피지 (추정)
    total: float            # 합계
    is_sell: bool           # 매도 여부


class KoreaTransactionCostModel:
    """한국 증권 거래비용 모델

    Usage:
        model = KoreaTransactionCostModel()

        # 단일 거래 비용
        cost = model.trade_cost(value=10_000_000, market=Market.KOSPI, is_sell=True)
        print(f"비용: {cost.total:,.0f}원 ({cost.total/10_000_000*100:.3f}%)")

        # 왕복 비용률
        rt = model.round_trip_rate(Market.KOSPI)
        print(f"왕복: {rt*100:.3f}%")

        # 연간 비용 추정 (회전율 기반)
        annual = model.annual_cost(n_roundtrips=12, market=Market.KOSPI)
        print(f"연간: {annual*100:.2f}%")

        # After-cost Kelly
        f = model.kelly_adjusted(mu=0.15, sigma=0.25, rf=0.035, n_roundtrips=12)
        print(f"Kelly: {f*100:.1f}%")
    """

    def __init__(
        self,
        fee_schedule: Optional[KoreaFeeSchedule] = None,
        slippage_bps: float = 5.0,
    ):
        self.fees = fee_schedule or KoreaFeeSchedule()
        self.slippage_bps = slippage_bps  # 기본 5bps (대형주 기준)

    def sell_tax_rate(self, market: Market = Market.KOSPI) -> float:
        """매도 시 세금률"""
        if market == Market.KOSPI:
            return self.fees.kospi_sell_tax
        return self.fees.kosdaq_sell_tax

    def buy_cost_rate(self) -> float:
        """매수 편도 비용률 (수수료 + 슬리피지)"""
        return self.fees.broker_commission + self.slippage_bps / 10_000

    def sell_cost_rate(self, market: Market = Market.KOSPI) -> float:
        """매도 편도 비용률 (수수료 + 세금 + 슬리피지)"""
        return (
            self.fees.broker_commission
            + self.sell_tax_rate(market)
            + self.slippage_bps / 10_000
        )

    def round_trip_rate(self, market: Market = Market.KOSPI) -> float:
        """왕복 비용률 = 매수 비용 + 매도 비용"""
        return self.buy_cost_rate() + self.sell_cost_rate(market)

    def trade_cost(
        self,
        value: float,
        market: Market = Market.KOSPI,
        is_sell: bool = False,
    ) -> TransactionCost:
        """단일 거래의 비용 계산

        Args:
            value: 거래 금액 (원)
            market: KOSPI 또는 KOSDAQ
            is_sell: 매도 여부
        """
        abs_value = abs(value)
        broker_fee = abs_value * self.fees.broker_commission
        tax = abs_value * self.sell_tax_rate(market) if is_sell else 0.0
        slippage = abs_value * self.slippage_bps / 10_000

        return TransactionCost(
            broker_fee=broker_fee,
            tax=tax,
            slippage=slippage,
            total=broker_fee + tax + slippage,
            is_sell=is_sell,
        )

    def annual_cost(
        self,
        n_roundtrips: float,
        market: Market = Market.KOSPI,
    ) -> float:
        """연간 거래비용 추정 (포트폴리오 비율)

        Args:
            n_roundtrips: 연간 왕복 거래 횟수 (12 = 월간 리밸런싱)
        """
        return n_roundtrips * self.round_trip_rate(market)

    def kelly_adjusted(
        self,
        mu: float,
        sigma: float,
        rf: float = 0.035,
        n_roundtrips: float = 12,
        market: Market = Market.KOSPI,
        fraction: float = 0.5,
    ) -> float:
        """After-cost Kelly 포지션 사이징

        f* = fraction × (μ - N×τ - rf) / σ²

        Half-Kelly (fraction=0.5): 75% 수익, 50% 변동성, 파산확률 극감.
        2×Kelly 초과 시 복리 수익 = 0 (수학적 증명).

        Args:
            mu: 기대 연간 수익률 (예: 0.15 = 15%)
            sigma: 연간 변동성 (예: 0.25 = 25%)
            rf: 무위험이자율 (예: 0.035 = 3.5%)
            n_roundtrips: 연간 왕복 거래 횟수
            market: KOSPI 또는 KOSDAQ
            fraction: Kelly 배수 (0.5 = Half-Kelly, 권장)

        Returns:
            최적 포지션 비율 (0~1). 음수면 0 반환.
        """
        annual_cost = self.annual_cost(n_roundtrips, market)
        excess_return = mu - annual_cost - rf

        if sigma <= 0 or excess_return <= 0:
            return 0.0

        full_kelly = excess_return / (sigma ** 2)
        return min(fraction * full_kelly, 1.0)

    def breakeven_alpha(
        self,
        n_roundtrips: float,
        market: Market = Market.KOSPI,
    ) -> float:
        """손익분기 알파 — 이 이상 수익이 나야 비용을 커버

        Args:
            n_roundtrips: 연간 왕복 거래 횟수
        """
        return self.annual_cost(n_roundtrips, market)

    def max_frequency(
        self,
        expected_alpha: float,
        market: Market = Market.KOSPI,
    ) -> float:
        """주어진 알파에서 최대 허용 연간 왕복 횟수

        Args:
            expected_alpha: 기대 연간 초과수익률 (비용 전)
        """
        rt = self.round_trip_rate(market)
        if rt <= 0:
            return float("inf")
        return expected_alpha / rt

    def summary(self, market: Market = Market.KOSPI) -> str:
        """비용 구조 요약 문자열"""
        return (
            f"=== 한국 거래비용 모델 ({market.value}) ===\n"
            f"매수: 수수료 {self.fees.broker_commission*100:.4f}%"
            f" + 슬리피지 {self.slippage_bps:.0f}bps"
            f" = {self.buy_cost_rate()*100:.3f}%\n"
            f"매도: 수수료 {self.fees.broker_commission*100:.4f}%"
            f" + 세금 {self.sell_tax_rate(market)*100:.2f}%"
            f" + 슬리피지 {self.slippage_bps:.0f}bps"
            f" = {self.sell_cost_rate(market)*100:.3f}%\n"
            f"왕복: {self.round_trip_rate(market)*100:.3f}%\n"
            f"---\n"
            f"월간 리밸런싱(12RT/yr): 연 {self.annual_cost(12, market)*100:.2f}%\n"
            f"주간 리밸런싱(50RT/yr): 연 {self.annual_cost(50, market)*100:.2f}%\n"
            f"일간 트레이딩(250RT/yr): 연 {self.annual_cost(250, market)*100:.1f}%\n"
            f"---\n"
            f"손익분기 알파(12RT): {self.breakeven_alpha(12, market)*100:.2f}%\n"
            f"손익분기 알파(50RT): {self.breakeven_alpha(50, market)*100:.2f}%\n"
        )
