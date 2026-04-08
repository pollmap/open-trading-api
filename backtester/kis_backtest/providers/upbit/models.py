"""Upbit 데이터 모델

업비트 API 응답을 불변 데이터클래스로 매핑.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class UpbitOrderSide(str, Enum):
    BID = "bid"   # 매수
    ASK = "ask"   # 매도


class UpbitOrderType(str, Enum):
    LIMIT = "limit"       # 지정가
    PRICE = "price"       # 시장가 매수 (총액)
    MARKET = "market"     # 시장가 매도 (수량)
    BEST = "best"         # 최유리


class UpbitOrderState(str, Enum):
    WAIT = "wait"
    WATCH = "watch"
    DONE = "done"
    CANCEL = "cancel"


@dataclass(frozen=True)
class UpbitMarket:
    """마켓 정보"""
    market: str            # "KRW-BTC"
    korean_name: str       # "비트코인"
    english_name: str      # "Bitcoin"
    market_warning: str = "NONE"

    @property
    def base(self) -> str:
        """기준 통화 (KRW, BTC, USDT)"""
        return self.market.split("-")[0]

    @property
    def coin(self) -> str:
        """코인 코드 (BTC, ETH ...)"""
        return self.market.split("-")[1]


@dataclass(frozen=True)
class UpbitCandle:
    """캔들 (OHLCV)"""
    market: str
    candle_date_time_utc: str
    candle_date_time_kst: str
    opening_price: float
    high_price: float
    low_price: float
    trade_price: float      # 종가
    candle_acc_trade_volume: float
    candle_acc_trade_price: float  # 거래대금
    timestamp: int = 0
    unit: int = 0           # 분 단위 (분봉일 때)

    @property
    def close(self) -> float:
        return self.trade_price

    @property
    def volume(self) -> float:
        return self.candle_acc_trade_volume


@dataclass(frozen=True)
class UpbitTicker:
    """현재가 정보"""
    market: str
    trade_price: float           # 현재가
    prev_closing_price: float    # 전일종가
    change: str                  # RISE, EVEN, FALL
    change_price: float          # 변동가
    change_rate: float           # 변동률
    acc_trade_volume_24h: float  # 24시간 거래량
    acc_trade_price_24h: float   # 24시간 거래대금
    highest_52_week_price: float = 0.0
    lowest_52_week_price: float = 0.0
    timestamp: int = 0

    @property
    def price(self) -> float:
        return self.trade_price


@dataclass(frozen=True)
class UpbitOrderbookUnit:
    """호가 단위"""
    ask_price: float    # 매도 호가
    bid_price: float    # 매수 호가
    ask_size: float     # 매도 잔량
    bid_size: float     # 매수 잔량


@dataclass(frozen=True)
class UpbitOrderbook:
    """호가 정보"""
    market: str
    timestamp: int
    total_ask_size: float
    total_bid_size: float
    orderbook_units: List[UpbitOrderbookUnit]

    @property
    def best_ask(self) -> float:
        return self.orderbook_units[0].ask_price if self.orderbook_units else 0.0

    @property
    def best_bid(self) -> float:
        return self.orderbook_units[0].bid_price if self.orderbook_units else 0.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid


@dataclass(frozen=True)
class UpbitTrade:
    """체결 내역"""
    market: str
    trade_date_utc: str
    trade_time_utc: str
    trade_price: float
    trade_volume: float
    ask_bid: str            # ASK or BID
    sequential_id: int = 0
    timestamp: int = 0


@dataclass(frozen=True)
class UpbitAccount:
    """계좌 잔고"""
    currency: str           # "KRW", "BTC"
    balance: float          # 보유량
    locked: float           # 주문 중 묶인 양
    avg_buy_price: float    # 매수 평균가
    avg_buy_price_modified: bool = False
    unit_currency: str = "KRW"

    @property
    def available(self) -> float:
        return self.balance - self.locked

    @property
    def total_value(self) -> float:
        """원화 환산 총 가치 (KRW일 때는 balance 그대로)"""
        if self.currency == "KRW":
            return self.balance
        return self.balance * self.avg_buy_price


@dataclass(frozen=True)
class UpbitOrder:
    """주문 정보"""
    uuid: str
    side: UpbitOrderSide
    ord_type: UpbitOrderType
    price: Optional[float]
    state: UpbitOrderState
    market: str
    volume: Optional[float]
    remaining_volume: Optional[float]
    executed_volume: float
    trades_count: int
    created_at: str
    paid_fee: float = 0.0
    remaining_fee: float = 0.0

    @property
    def is_filled(self) -> bool:
        return self.state == UpbitOrderState.DONE

    @property
    def fill_rate(self) -> float:
        if self.volume and float(self.volume) > 0:
            return self.executed_volume / float(self.volume)
        return 0.0


# ── 팩토리 함수 ────────────────────────────────────

def _parse_market(data: Dict[str, Any]) -> UpbitMarket:
    return UpbitMarket(
        market=data["market"],
        korean_name=data.get("korean_name", ""),
        english_name=data.get("english_name", ""),
        market_warning=data.get("market_warning", "NONE"),
    )


def _parse_candle(data: Dict[str, Any]) -> UpbitCandle:
    return UpbitCandle(
        market=data["market"],
        candle_date_time_utc=data.get("candle_date_time_utc", ""),
        candle_date_time_kst=data.get("candle_date_time_kst", ""),
        opening_price=float(data.get("opening_price", 0)),
        high_price=float(data.get("high_price", 0)),
        low_price=float(data.get("low_price", 0)),
        trade_price=float(data.get("trade_price", 0)),
        candle_acc_trade_volume=float(data.get("candle_acc_trade_volume", 0)),
        candle_acc_trade_price=float(data.get("candle_acc_trade_price", 0)),
        timestamp=data.get("timestamp", 0),
        unit=data.get("unit", 0),
    )


def _parse_ticker(data: Dict[str, Any]) -> UpbitTicker:
    return UpbitTicker(
        market=data["market"],
        trade_price=float(data.get("trade_price", 0)),
        prev_closing_price=float(data.get("prev_closing_price", 0)),
        change=data.get("change", "EVEN"),
        change_price=float(data.get("change_price", 0)),
        change_rate=float(data.get("change_rate", 0)),
        acc_trade_volume_24h=float(data.get("acc_trade_volume_24h", 0)),
        acc_trade_price_24h=float(data.get("acc_trade_price_24h", 0)),
        highest_52_week_price=float(data.get("highest_52_week_price", 0)),
        lowest_52_week_price=float(data.get("lowest_52_week_price", 0)),
        timestamp=data.get("timestamp", 0),
    )


def _parse_orderbook(data: Dict[str, Any]) -> UpbitOrderbook:
    units = [
        UpbitOrderbookUnit(
            ask_price=float(u.get("ask_price", 0)),
            bid_price=float(u.get("bid_price", 0)),
            ask_size=float(u.get("ask_size", 0)),
            bid_size=float(u.get("bid_size", 0)),
        )
        for u in data.get("orderbook_units", [])
    ]
    return UpbitOrderbook(
        market=data["market"],
        timestamp=data.get("timestamp", 0),
        total_ask_size=float(data.get("total_ask_size", 0)),
        total_bid_size=float(data.get("total_bid_size", 0)),
        orderbook_units=units,
    )


def _parse_account(data: Dict[str, Any]) -> UpbitAccount:
    return UpbitAccount(
        currency=data["currency"],
        balance=float(data.get("balance", 0)),
        locked=float(data.get("locked", 0)),
        avg_buy_price=float(data.get("avg_buy_price", 0)),
        avg_buy_price_modified=data.get("avg_buy_price_modified", False),
        unit_currency=data.get("unit_currency", "KRW"),
    )


def _parse_order(data: Dict[str, Any]) -> UpbitOrder:
    return UpbitOrder(
        uuid=data["uuid"],
        side=UpbitOrderSide(data["side"]),
        ord_type=UpbitOrderType(data["ord_type"]),
        price=float(data["price"]) if data.get("price") else None,
        state=UpbitOrderState(data["state"]),
        market=data["market"],
        volume=float(data["volume"]) if data.get("volume") else None,
        remaining_volume=float(data["remaining_volume"]) if data.get("remaining_volume") else None,
        executed_volume=float(data.get("executed_volume", 0)),
        trades_count=int(data.get("trades_count", 0)),
        created_at=data.get("created_at", ""),
        paid_fee=float(data.get("paid_fee", 0)),
        remaining_fee=float(data.get("remaining_fee", 0)),
    )
