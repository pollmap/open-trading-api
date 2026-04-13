"""Upbit `BrokerageProvider` adapter (v1.2).

UpbitClient(기존)을 LuxonTerminal의 BrokerageProvider Protocol에 맞춰 어댑트.
KRW 기반 현물 거래만 지원 (파생/마진 X).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from kis_backtest.models import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from kis_backtest.providers.upbit.client import UpbitClient
from kis_backtest.providers.upbit.models import (
    UpbitOrderSide,
    UpbitOrderType,
)

logger = logging.getLogger(__name__)


class UpbitBrokerageProvider:
    """`BrokerageProvider` impl for Upbit spot trading.

    Symbols use Upbit format: ``KRW-BTC``, ``KRW-ETH``, etc.
    """

    def __init__(
        self,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
    ) -> None:
        self._client = UpbitClient(access_key=access_key, secret_key=secret_key)
        logger.info("UpbitBrokerageProvider initialized")

    def get_balance(self) -> AccountBalance:
        accounts = self._client.get_accounts()
        total_krw = 0.0
        available_krw = 0.0
        locked_krw = 0.0
        for a in accounts:
            if a.currency == "KRW":
                total_krw = float(a.balance) + float(a.locked)
                available_krw = float(a.balance)
                locked_krw = float(a.locked)
            else:
                # Non-KRW assets — convert to KRW at avg_buy_price for equity
                try:
                    qty = float(a.balance) + float(a.locked)
                    total_krw += qty * float(a.avg_buy_price)
                except Exception:
                    pass

        return AccountBalance(
            total_cash=available_krw + locked_krw,
            available_cash=available_krw,
            total_equity=total_krw,
            total_pnl=0.0,
            total_pnl_percent=0.0,
            currency="KRW",
        )

    def get_positions(self) -> List[Position]:
        accounts = self._client.get_accounts()
        positions: List[Position] = []
        for a in accounts:
            if a.currency == "KRW":
                continue
            qty = float(a.balance)
            if qty <= 0:
                continue
            avg = float(a.avg_buy_price)
            symbol = f"KRW-{a.currency}"
            positions.append(Position(
                symbol=symbol,
                quantity=int(qty) if qty.is_integer() else qty,
                average_price=avg,
                current_price=avg,  # 정확한 현재가는 get_ticker 호출 필요
                unrealized_pnl=0.0,
                unrealized_pnl_percent=0.0,
                name=a.currency,
            ))
        return positions

    def submit_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Order:
        up_side = UpbitOrderSide.BID if side == OrderSide.BUY else UpbitOrderSide.ASK

        # Upbit MARKET: 매수는 금액(price) 지정, 매도는 수량
        if order_type == OrderType.MARKET:
            if side == OrderSide.BUY:
                if price is None:
                    raise ValueError("Upbit market buy requires price (KRW amount)")
                result = self._client.place_order(
                    market=symbol, side=up_side,
                    ord_type=UpbitOrderType.PRICE,
                    price=price,
                )
            else:
                result = self._client.place_order(
                    market=symbol, side=up_side,
                    ord_type=UpbitOrderType.MARKET,
                    volume=float(quantity),
                )
        else:
            if price is None:
                raise ValueError("Limit order requires price")
            result = self._client.place_order(
                market=symbol, side=up_side,
                ord_type=UpbitOrderType.LIMIT,
                price=price, volume=float(quantity),
            )

        logger.info("Upbit order placed: %s %s qty=%s", symbol, side.value, quantity)
        return Order(
            id=result.uuid,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            filled_quantity=int(float(result.executed_volume or 0)),
            average_price=0.0,
            status=OrderStatus.SUBMITTED,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )


class UpbitPriceAdapter:
    """`PriceProvider` adapter — Upbit ticker 기반 현재가."""

    def __init__(self, brokerage: UpbitBrokerageProvider) -> None:
        self._client = brokerage._client

    def get_current_price(self, symbol: str) -> float:
        try:
            tickers = self._client.get_tickers([symbol])
            if tickers:
                return float(tickers[0].trade_price)
        except Exception as exc:
            logger.warning("Upbit price fetch failed %s: %s", symbol, exc)
        return 0.0
